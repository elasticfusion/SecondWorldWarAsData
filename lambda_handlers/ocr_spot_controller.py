"""OCR SPOT controller (backlog #2, Option B — hourly spot-retry / 48h on-demand cap).

Operator design: prefer SPOT for GPU OCR (max savings); if a job can't get spot
capacity for a while, the CUSTOM controller routes it to an on-demand fallback
queue, capped at 48h cumulative on-demand time — then it's abandoned/alerted, not
looped forever.

Runs on an hourly EventBridge schedule. Each run:
  1. WARN if running spot vCPU >= 80% of the live G/VT spot quota (L-3819A6DF),
     with an actionable quota-increase link (the quota is soft/raisable).
  2. For each job stuck RUNNABLE on the SPOT queue longer than SPOT_WAIT_SECS:
     resubmit an equivalent job to the ON-DEMAND queue, terminate the spot job,
     and record the move in ocrctl#{ident} (so it's not re-moved / double-OCR'd).
  3. For each controller-tracked on-demand job past 48h cumulative: terminate +
     alert (the 48h cap) rather than pay indefinitely.

Idempotent: a job identity (jobName) is moved at most once; the ocrctl# record is
the source of truth. Self-contained; no heavy imports.

Task 1.1d (owner 2026-11-18/20): the Spot side is a LADDER of instance types
(src/utils/gpu_ladder). A job that is reclaimed (``Host EC2 ... terminated``; the job definition
does not retry that on the same queue) or starved on rung k moves to the next rung that can run
the workload and whose live Spot price is at or under the ORIGINAL (rung 0) type's on-demand
price; when none is left it goes to the on-demand queue, which holds the original type only.
A reclaim is handled at once (EventBridge: Batch job FAILED) and by the hourly sweep.
"""

import json
import logging
import os
import time
from typing import Optional

import boto3

from src.utils import gpu_ladder

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
CACHE_TABLE = os.getenv("CACHE_TABLE", f"{ENV_NAME}-wwii-api-cache")
ONDEMAND_QUEUE = os.getenv(
    "OCR_ONDEMAND_QUEUE", f"{ENV_NAME}-wwii-chandra-gpu-ondemand"
)
# Watchdog exit code meaning "GPU OOM" (see ocr_watchdog). With a 24GB VRAM floor
# on all Chandra pools this is exceptional and flagged for review (not retried).
EXIT_OOM = 76
JOB_DEF = os.getenv("OCR_JOB_DEF", f"{ENV_NAME}-wwii-chandra")
NOTIFICATION_TOPIC_ARN = os.getenv("NOTIFICATION_TOPIC_ARN", "")

# The ladder: instance types in rung order (rung 0 = the ORIGINAL type, whose on-demand hourly price
# is the cap); one Spot queue per rung. Absent: a single rung (no climbing, on-demand fallback only).
LADDER = gpu_ladder.parse_ladder(os.getenv("OCR_GPU_LADDER", "")) or ["g5.xlarge"]
LADDER[0] = LADDER[0] or "g5.xlarge"
MAX_FRACTION = gpu_ladder.max_fraction(os.getenv("OCR_MAX_FRACTION", "1.0"))
if str(os.getenv("OCR_MAX_FRACTION", "1.0")) not in (str(MAX_FRACTION), "1.0"):
    logger.error(
        "OBSERV ocr.max_fraction_ignored value=%s used=%s status=error - OCR_MAX_FRACTION may "
        "tighten the limit (0 < f <= 1), never loosen it; fix the controller's environment",
        os.getenv("OCR_MAX_FRACTION"),
        MAX_FRACTION,
    )
MIN_GPU_MIB = int(os.getenv("OCR_MIN_GPU_MIB", str(gpu_ladder.MIN_GPU_MIB)))
SPOT_QUEUES = gpu_ladder.queue_names(ENV_NAME, len(LADDER))
SUBNET_IDS = [x for x in os.getenv("PRIVATE_SUBNET_IDS", "").split(",") if x]
# A reclaimed job is looked for this long after it was created (a job runs for up to 4 h)
RECLAIM_LOOKBACK_S = int(os.getenv("OCR_RECLAIM_LOOKBACK_SECS", str(12 * 3600)))
CLAIM_TTL_S = 7 * 86400

# A spot job RUNNABLE longer than this (no capacity) is routed to on-demand.
SPOT_WAIT_SECS = int(os.getenv("OCR_SPOT_WAIT_SECS", "3600"))  # 1h
# Cumulative on-demand time cap per job.
ONDEMAND_CAP_SECS = int(os.getenv("OCR_ONDEMAND_CAP_SECS", str(48 * 3600)))  # 48h
# G/VT Spot Instance Requests quota (soft, raisable).
_SPOT_QUOTA_CODE = "L-3819A6DF"
_PER_JOB_VCPU = 4


def _batch():
    return boto3.client("batch", region_name=REGION)


def _table():
    return boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)


def _notify(subject: str, message: str) -> None:
    if not NOTIFICATION_TOPIC_ARN:
        return
    try:
        boto3.client("sns", region_name=REGION).publish(
            TopicArn=NOTIFICATION_TOPIC_ARN, Subject=subject, Message=message
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("notify failed: %s", e)


def _spot_quota_vcpus() -> int:
    try:
        sq = boto3.client("service-quotas", region_name=REGION)
        return int(
            sq.get_service_quota(ServiceCode="ec2", QuotaCode=_SPOT_QUOTA_CODE)[
                "Quota"
            ]["Value"]
        )
    except Exception as e:
        logger.warning("spot quota lookup failed: %s", e)
        return 0


def _list_jobs(queue: str, status: str, **filters) -> list:
    """The queue's jobs in ``status`` (all pages); [] for a queue that does not exist (a ladder
    deployed without its upper rungs)."""
    from botocore.exceptions import ClientError

    jobs = []
    try:
        paginator = _batch().get_paginator("list_jobs")
        for page in paginator.paginate(jobQueue=queue, jobStatus=status, **filters):
            jobs.extend(page.get("jobSummaryList", []))
    except ClientError as e:
        if "does not exist" in str(e) or "not found" in str(e).lower():
            return []
        raise
    return jobs


def _warn_if_near_quota() -> None:
    """§5.0: warn when running spot vCPU >= 80% of the live spot quota."""
    quota = _spot_quota_vcpus()
    if quota <= 0:
        return
    running = [
        j
        for q in SPOT_QUEUES
        for status in ("RUNNING", "STARTING")
        for j in _list_jobs(q, status)
    ]
    used_vcpu = len(running) * _PER_JOB_VCPU
    if used_vcpu >= 0.8 * quota:
        _notify(
            "WWII Pipeline: OCR spot near quota ceiling",
            (
                f"OCR spot usage {used_vcpu} vCPU is >=80% of the G/VT spot quota "
                f"({quota} vCPU). Request an increase to raise OCR throughput: "
                f"https://console.aws.amazon.com/servicequotas/home/services/ec2/quotas/"
                f"{_SPOT_QUOTA_CODE} (quota {_SPOT_QUOTA_CODE})."
            ),
        )


def _claim_move(job: dict, ident: str, ondemand: bool) -> Optional[dict]:
    """Take the claim that makes this job's move happen once. Returns the PRIOR row ({} when there
    was none) if the claim is ours, or None if the job was already moved / is held by a live job.

    The ocrctl#{name} claim prevents moving the SAME live job twice. A claim left over from a PRIOR
    job of the same name (now terminal: an earlier rung's job, or an earlier run) must not block a
    freshly-submitted job forever, so a claim whose recorded spot_job_id is terminal is stale and
    overwritten. (Bug: a stale claim silently no-op'd every hourly re-route.)"""
    table = _table()
    now = int(time.time())
    claim = {
        "cache_key": ident,
        "spot_job_id": job["jobId"],
        "moved_from": {
            job["jobId"]
        },  # every job this claim has moved (never moved twice)
        "claimed_at": now,
        "ttl": now + CLAIM_TTL_S,
    }
    if ondemand:
        claim["ondemand_started_at"] = now
    try:
        table.put_item(
            Item=claim, ConditionExpression="attribute_not_exists(cache_key)"
        )
        return {}
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        existing = table.get_item(Key={"cache_key": ident}).get("Item", {})
        if job["jobId"] in _handled_ids(existing):
            logger.info("Already routed %s (same job) — skip", job["jobName"])
            return None
        if not _claim_is_stale(existing, job["jobId"]):
            logger.info("Route claim for %s held by a live job — skip", job["jobName"])
            return None
        logger.info(
            "Stale route claim for %s (prior job terminal) — reclaiming", job["jobName"]
        )
        claim["moves"] = existing.get("moves", 0)
        claim["moved_from"] = _handled_ids(existing) | {job["jobId"]}
        if "ondemand_started_at" in existing and ondemand:
            if existing.get("resubmitted_job_id") == job["jobId"]:  # the same chain
                claim["ondemand_started_at"] = existing[
                    "ondemand_started_at"
                ]  # 48 h cap
        try:
            # compare-and-set: of two handlers (event + sweep, a duplicate event) only one wins
            table.put_item(
                Item=claim,
                ConditionExpression="spot_job_id = :p",
                ExpressionAttributeValues={":p": existing.get("spot_job_id")},
            )
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            logger.info(
                "Route claim for %s taken by another handler — skip", job["jobName"]
            )
            return None
        return existing


def _handled_ids(row: dict) -> set:
    """Every job id this claim row has already moved (the one it holds included)."""
    return {
        x for x in (set(row.get("moved_from") or ()) | {row.get("spot_job_id")}) if x
    }


def _claim_is_stale(row: dict, job_id: str) -> bool:
    """True if every job the claim points at (other than ``job_id``, the one being handled) is
    terminal: the claim is left over from a finished earlier job of the same name."""
    live = {row.get("spot_job_id"), row.get("resubmitted_job_id")} - {job_id, None}
    return all(_job_is_terminal(j) for j in live)


def _resubmit(
    job: dict,
    queue: str,
    ondemand: bool,
    rung: Optional[int] = None,
    terminate: bool = True,
) -> bool:
    """Resubmit ``job`` (its container command, so the same input/output/page-range is OCR'd) to
    ``queue``, terminate the old job if it is still live (``terminate``), and record the move.
    Returns True iff a move actually happened."""
    name = job["jobName"]
    ident = f"ocrctl#{name}"
    table = _table()
    prior = _claim_move(job, ident, ondemand)
    if prior is None:
        return False
    batch = _batch()
    extra: dict = {}
    if ondemand:
        # the job definition does not retry a Spot reclaim (the ladder moves the job); an on-demand
        # job has nowhere to move, so it keeps the plain 2 attempts
        extra["retryStrategy"] = {"attempts": 2}
    try:
        desc = batch.describe_jobs(jobs=[job["jobId"]])["jobs"]
        cmd = desc[0].get("container", {}).get("command", []) if desc else []
        resp = batch.submit_job(
            jobName=name[:128],
            jobQueue=queue,
            jobDefinition=JOB_DEF,
            containerOverrides={"command": cmd} if cmd else {},
            **extra,
        )
    except Exception as e:
        # nothing was submitted: give the claim back as it was (the earlier row, with the jobs it
        # already moved, or none) so the next hourly run retries
        if prior:
            table.put_item(Item=prior)
        else:
            table.delete_item(Key={"cache_key": ident})
        logger.error(
            "OBSERV ocr.resubmit status=error job=%s queue=%s err=%s - the job is retried "
            "by the next hourly run",
            name,
            queue,
            type(e).__name__,
        )
        return False
    # The new job is running: the claim is KEPT whatever happens next (releasing it would let the
    # next sweep submit a second copy).
    _record_move(job, ident, queue, resp["jobId"], prior, ondemand, rung)
    if terminate:
        try:
            batch.terminate_job(jobId=job["jobId"], reason=f"rerouted to {queue}")
        except Exception as e:
            logger.error(
                "OBSERV ocr.resubmit_terminate status=error job=%s old_job=%s err=%s - the job "
                "WAS resubmitted; terminate the old job by hand",
                name,
                job["jobId"],
                type(e).__name__,
            )
    logger.info("Routed %s -> %s (%s)", name, queue, resp["jobId"])
    return True


def _record_move(job, ident, queue, new_id, prior, ondemand, rung) -> None:
    """Write the new job onto the claim (the 48 h cap finds an on-demand job through it). A failure
    is logged, never raised: the job IS running."""
    values = {":j": new_id, ":q": queue, ":n": int(prior.get("moves", 0)) + 1}
    update = "SET resubmitted_job_id = :j, queue = :q, moves = :n"
    if ondemand:
        update += ", ondemand_job_id = :j"
    if rung is not None:
        update += ", rung = :r"
        values[":r"] = rung
    try:
        _table().update_item(
            Key={"cache_key": ident},
            UpdateExpression=update,
            ExpressionAttributeValues=values,
        )
    except Exception as e:
        logger.error(
            "OBSERV ocr.resubmit_bookkeeping status=error job=%s new_job=%s err=%s - the job "
            "WAS resubmitted; check that %s is terminated",
            job["jobName"],
            new_id,
            type(e).__name__,
            job["jobId"],
        )


def _resubmit_to_ondemand(job: dict) -> bool:
    """Resubmit a spot-starved job to the on-demand queue (the original instance type only),
    terminate the spot job, and record the move (the 48 h on-demand cap counts from here).
    """
    return _resubmit(job, ONDEMAND_QUEUE, ondemand=True)


_azs_cache: list = []


def _azs() -> list:
    """The AZs of the subnets the compute environments use (a Spot price in an AZ they cannot
    use does not count)."""
    if not _azs_cache and SUBNET_IDS:
        try:
            subnets = boto3.client("ec2", region_name=REGION).describe_subnets(
                SubnetIds=SUBNET_IDS
            )["Subnets"]
            _azs_cache.extend(sorted({s["AvailabilityZone"] for s in subnets}))
        except (
            Exception
        ) as e:  # noqa: BLE001 - every AZ's price is then taken (more cautious)
            logger.warning("subnet AZ lookup failed (all AZs priced): %s", e)
    return list(_azs_cache)


def _price_clients():
    return (
        boto3.client("ec2", region_name=REGION),
        boto3.client("pricing", region_name="us-east-1"),
    )


def _next_move(rung: int) -> "gpu_ladder.Move":
    """Where a job that failed on Spot ``rung`` goes next (src/utils/gpu_ladder.next_move). Live
    prices are read only when a later rung exists; any failure reading them gives the on-demand
    queue at the ORIGINAL type (never above the owner's cap)."""
    later = LADDER[rung + 1 :]
    shapes: dict = {}
    spot: dict = {}
    od = None
    if later:
        try:
            ec2, pricing = _price_clients()
            od = gpu_ladder.load_on_demand(_table(), pricing, LADDER[0], REGION)
            azs = _azs()
            for t in (x for x in later if x):
                shapes[t] = gpu_ladder.shape_of(ec2, t)
                spot[t] = gpu_ladder.spot_hourly(ec2, t, azs)
        except Exception as e:  # noqa: BLE001 - unknown prices: no climbing
            logger.error(
                "OBSERV ocr.ladder_prices_unavailable err=%s status=error - the job goes "
                "on-demand at %s (the original type); check the controller's pricing / ec2 "
                "permissions",
                type(e).__name__,
                LADDER[0],
            )
            shapes, spot = {}, {}
    return gpu_ladder.next_move(
        ENV_NAME, LADDER, rung, shapes, spot, od, MAX_FRACTION, MIN_GPU_MIB
    )


def _already_moved(job: dict) -> bool:
    row = (
        _table().get_item(Key={"cache_key": f"ocrctl#{job['jobName']}"}).get("Item", {})
    )
    return job["jobId"] in _handled_ids(row)


def _move_job(job: dict, rung: int, why: str, terminate: bool) -> bool:
    """Move a job that failed (``why``: reclaimed / starved) on Spot ``rung`` to the next place.
    Alerts a reclaim and any move to on-demand (it costs more); logs a Spot-to-Spot move.
    """
    if _already_moved(
        job
    ):  # the hourly sweep lists old failures: no price calls for them
        return False
    move = _next_move(rung)
    if move.ondemand:
        moved = (
            _resubmit_to_ondemand(job)
            if terminate
            else _resubmit(job, move.queue, ondemand=True, terminate=False)
        )
    else:
        moved = _resubmit(
            job, move.queue, ondemand=False, rung=move.rung, terminate=terminate
        )
    if not moved:
        return False
    skipped = f" Passed over: {'; '.join(move.skipped)}." if move.skipped else ""
    line = (
        f"OCR job {job['jobName']} was {why} on {LADDER[rung]} Spot (rung {rung}); resubmitted "
        f"on {move.describe()}.{skipped}"
    )
    logger.info("OBSERV ocr.ladder_move status=ok %s", line)
    if (
        move.ondemand
    ):  # it costs more, so the operator is told; a Spot-to-Spot move is only logged
        _notify(
            "WWII Pipeline: OCR job moved to on-demand",
            f"{line} It is capped at {ONDEMAND_CAP_SECS // 3600} h on-demand. To avoid this, check "
            "Spot capacity/price for the ladder types (OcrOperations: GpuRung*Type parameters).",
        )
    return True


def _job_is_terminal(job_id) -> bool:
    """True if the Batch job is gone or in a terminal state (SUCCEEDED/FAILED).
    A missing/undescribable job is treated as terminal (nothing live to protect).
    Used to detect a stale route claim left by a prior job of the same name."""
    if not job_id:
        return True
    try:
        jobs = _batch().describe_jobs(jobs=[job_id]).get("jobs", [])
        if not jobs:
            return True
        return jobs[0].get("status") in ("SUCCEEDED", "FAILED")
    except Exception as e:  # pragma: no cover - defensive; assume NOT terminal
        logger.warning("Could not describe %s (%s) — treating as live", job_id, e)
        return False


def _over_cap(rung: int) -> Optional[str]:
    """Why Spot rung ``rung`` is over the owner's limit right now (its live Spot price is above
    the original type's on-demand price x MAX_FRACTION), or None. Prices that cannot be read give
    None (the rung is then left alone; the next move still checks them)."""
    t = LADDER[rung]
    if rung == 0 or not t:
        return None
    try:
        ec2, pricing = _price_clients()
        od = gpu_ladder.load_on_demand(_table(), pricing, LADDER[0], REGION)
        price = gpu_ladder.spot_hourly(ec2, t, _azs())
    except Exception as e:  # noqa: BLE001
        logger.error(
            "OBSERV ocr.cap_check status=error rung=%d err=%s - the price check of a waiting "
            "job was skipped; check the controller's pricing / ec2 permissions",
            rung,
            type(e).__name__,
        )
        return None
    if od and price and price > od * MAX_FRACTION:
        return f"{t} Spot ${price:.4f}/h is over ${od * MAX_FRACTION:.4f}/h"
    return None


def _route_spot_starved() -> int:
    """Move jobs waiting RUNNABLE on a Spot rung to the next rung (or on-demand) when they have
    waited > SPOT_WAIT_SECS, or at once when the rung's live Spot price has risen over the limit
    (a job that launched at that price would break it). Counts only jobs ACTUALLY moved.
    """
    now = int(time.time())
    routed = 0
    for rung, queue in enumerate(SPOT_QUEUES):
        jobs = _list_jobs(queue, "RUNNABLE")
        over = _over_cap(rung) if jobs else None
        for job in jobs:
            created = int(job.get("createdAt", now * 1000)) / 1000  # ms -> s
            if over:
                why = f"waiting while {over}"
            elif now - created >= SPOT_WAIT_SECS:
                why = "starved (no Spot capacity)"
            else:
                continue
            if _move_job(job, rung, why, terminate=True):
                routed += 1
    return routed


def _is_reclaim(job: dict) -> bool:
    """True if Batch failed the job because its Spot instance was reclaimed: the job's final
    status reason (else its last attempt's). An earlier attempt's reclaim does not count.
    """
    attempts = job.get("attempts") or []
    reason = job.get("statusReason") or (
        attempts[-1].get("statusReason") if attempts else ""
    )
    return "Host EC2" in str(reason or "")


def _handle_reclaim(job: dict, queue: str) -> bool:
    """A FAILED job on ``queue`` whose Spot instance was reclaimed moves up the ladder. Returns
    True iff it was moved now."""
    rung = gpu_ladder.rung_of(queue, ENV_NAME, len(LADDER))
    # ListJobs ignores jobStatus when a filter is given (the sweep), so the status is checked here
    if rung is None or job.get("status") != "FAILED" or not _is_reclaim(job):
        return False
    return _move_job(job, rung, "reclaimed", terminate=False)


def _sweep_reclaims() -> int:
    """The hourly backstop for the immediate EventBridge path: reclaimed jobs that failed in the
    last RECLAIM_LOOKBACK_S on any Spot rung. A job already moved is a no-op (its claim).
    """
    since = (int(time.time()) - RECLAIM_LOOKBACK_S) * 1000
    moved = 0
    for queue in SPOT_QUEUES:
        failed = _list_jobs(
            queue,
            "FAILED",
            filters=[{"name": "AFTER_CREATED_AT", "values": [str(since)]}],
        )
        for job in failed:
            if _handle_reclaim(job, queue):
                moved += 1
    return moved


def _flag_oom_failures() -> int:
    """Flag OOM-failed OCR jobs (watchdog exit 76) for human review + alert.

    With the 24GB-VRAM floor on ALL Chandra pools, a CUDA OOM is EXCEPTIONAL (a
    single page needing >~23GB — e.g. an enormous fold-out plate). Bouncing it to
    another 24GB queue would not help, so we do NOT loop: alert once (email+Slack)
    so a human can decide (down-sample the page / split it / larger instance).
    One alert per job (ocrctl#oom#{name} claim). Returns the count flagged."""
    flagged = 0
    for queue in (*SPOT_QUEUES, ONDEMAND_QUEUE):
        for job in _list_jobs(queue, "FAILED"):
            desc = _batch().describe_jobs(jobs=[job["jobId"]])["jobs"]
            if not desc:
                continue
            container = desc[0].get("container", {})
            if container.get("exitCode") != EXIT_OOM:
                continue
            name = job["jobName"]
            ident = f"ocrctl#oom#{name}"
            table = _table()
            try:
                table.put_item(
                    Item={
                        "cache_key": ident,
                        "flagged_at": int(time.time()),
                        "oom_job_id": job["jobId"],
                    },
                    ConditionExpression="attribute_not_exists(cache_key)",
                )
            except table.meta.client.exceptions.ConditionalCheckFailedException:
                continue  # already alerted once
            _notify(
                "WWII Pipeline: OCR job OOM on 24GB GPU — needs review",
                f"OCR job {name} hit CUDA out-of-memory even on a 24GB GPU "
                f"(job {job['jobId']}). This is exceptional — likely an oversized "
                f"page. It needs human review: down-sample/split the page or use a "
                f"larger instance. It will NOT be retried automatically.",
            )
            logger.warning("OOM on 24GB — flagged %s for review", name)
            flagged += 1
    return flagged


def _ondemand_rows() -> list:
    """Every ``ocrctl#`` row with a live on-demand job not yet capped (all scan pages)."""
    table = _table()
    kw: dict = {
        "FilterExpression": "begins_with(cache_key, :p) AND attribute_exists(ondemand_job_id) "
        "AND attribute_not_exists(capped_at)",
        "ExpressionAttributeValues": {":p": "ocrctl#"},
    }
    rows: list = []
    while True:
        resp = table.scan(**kw)
        rows.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            return rows
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _enforce_ondemand_cap() -> int:
    """Terminate + alert (once) on-demand jobs past the 48h cumulative cap."""
    now = int(time.time())
    capped = 0
    for item in _ondemand_rows():
        started = int(item.get("ondemand_started_at", now))
        if now - started >= ONDEMAND_CAP_SECS:
            jid = item.get("ondemand_job_id")
            try:
                _batch().terminate_job(jobId=jid, reason="48h on-demand cap (§2)")
                _table().update_item(
                    Key={"cache_key": item["cache_key"]},
                    UpdateExpression="SET capped_at = :n",
                    ExpressionAttributeValues={":n": now},
                )
                _notify(
                    "WWII Pipeline: OCR job hit 48h on-demand cap",
                    f"OCR job {item['cache_key']} exceeded the 48h on-demand cap and "
                    f"was terminated. It needs investigation (persistent spot "
                    f"unavailability or a stuck doc).",
                )
                capped += 1
            except Exception as e:
                logger.warning("cap-terminate failed for %s: %s", jid, e)
    return capped


def _handle_failed_event(detail: dict) -> dict:
    """EventBridge: a Batch job FAILED. A Spot reclaim on a ladder queue is moved up at once. The
    job is read back from Batch (queue, name, status), not trusted from the event."""
    job_id = detail.get("jobId", "")
    if not job_id:
        return {"action": "none", "reason": "no job id"}
    desc = _batch().describe_jobs(jobs=[job_id])["jobs"]
    if not desc or desc[0].get("status") != "FAILED":
        return {"action": "none", "reason": "not a FAILED job"}
    job = {**desc[0], "jobId": job_id}
    queue = str(job.get("jobQueue", "")).rsplit("/", 1)[-1]
    if gpu_ladder.rung_of(queue, ENV_NAME, len(LADDER)) is None:
        return {"action": "none", "reason": "not a ladder job"}
    moved = _handle_reclaim(job, queue)
    return {"action": "moved" if moved else "none", "job": job.get("jobName")}


def _check_original_type() -> None:
    """The original type (rung 0) sets the cap and is the on-demand fallback, so it is checked
    like every later rung: it must be able to run the job and its on-demand price must be known.
    """
    try:
        ec2, pricing = _price_clients()
        shape = gpu_ladder.shape_of(ec2, LADDER[0])
        od = gpu_ladder.load_on_demand(_table(), pricing, LADDER[0], REGION)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "OBSERV ocr.original_type_check status=error err=%s - cannot verify %s; check the "
            "controller's pricing / ec2 permissions",
            type(e).__name__,
            LADDER[0],
        )
        return
    if not gpu_ladder.capable(shape, MIN_GPU_MIB):
        logger.error(
            "OBSERV ocr.original_type_unfit type=%s status=error - the original GPU type cannot "
            "run the OCR job (needs 1 GPU >= %d MiB, %d vCPU, %d MiB); set GpuRung0Type in "
            "cloudformation/ocr.yaml to a capable type",
            LADDER[0],
            MIN_GPU_MIB,
            gpu_ladder.JOB_VCPU,
            gpu_ladder.JOB_MEMORY_MIB,
        )
    if od is None:
        logger.error(
            "OBSERV ocr.original_price_unknown type=%s status=error - the on-demand price (the "
            "cap) cannot be read, so no job climbs the ladder; check pricing:GetProducts",
            LADDER[0],
        )


def handler(event, _context):
    """A Spot job FAILED (event): move a reclaimed job up the ladder now. Otherwise (hourly): warn
    near quota, move reclaimed + spot-starved jobs, flag OOMs, enforce the 48h cap."""
    if (event or {}).get("source") == "aws.batch":
        return _handle_failed_event(event.get("detail", {}))
    _warn_if_near_quota()
    _check_original_type()
    reclaimed = _sweep_reclaims()
    routed = _route_spot_starved()
    oom_flagged = _flag_oom_failures()
    capped = _enforce_ondemand_cap()
    logger.info(
        "OCR controller: reclaimed=%d routed=%d oom_flagged=%d capped=%d",
        reclaimed,
        routed,
        oom_flagged,
        capped,
    )
    return {
        "reclaimed": reclaimed,
        "routed": routed,
        "oom_flagged": oom_flagged,
        "capped": capped,
    }
