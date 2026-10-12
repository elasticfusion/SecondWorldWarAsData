"""Dispatcher Lambda handlers for the M4.3 Step Functions Map dispatcher (§17).

Three thin handlers the state machine invokes:
- enumerate_pending: list documents not done / not in-flight (§8 lifecycle), as
  heterogeneous work items each carrying its routed task_def + book (§7).
- clamp_pool: compute the effective Map MaxConcurrency = min(pool_max, quota cap),
  floored at pool_min (§5.0). Queries the live Fargate vCPU quota.
- human_gate: store a waitForTaskToken so a review gate can resume the branch.

Kept deliberately small; the heavy coordination lives in the ECS task + DynamoDB
(§13 "SFN orchestrates; DynamoDB coordinates").
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
CACHE_TABLE = os.getenv("CACHE_TABLE", f"{ENV_NAME}-wwii-api-cache")

# Fargate on-demand vCPU quota code (§5.1). Soft, raisable.
_FARGATE_VCPU_QUOTA = "L-3032A538"
# Per-task vCPU assumption for the pool→vCPU headroom check (matches §7.1 profiles).
_PER_TASK_VCPU = int(os.getenv("PER_TASK_VCPU", "1"))

_LIFECYCLE_DONE = {"done", "failed", "needs-review"}
# A doc is RUNNING (a task actively working it) -> not dispatchable. The dispatcher
# sets this atomically on claim; the ECS task advances it on phase completion.
_RUNNING = "running"
# READY states: a phase can be launched. Each carries next_phase (the phase to run).
# held_unprocessed->phase1, parsed->phase2, extracted/deduped->phase3.
_READY = {"held_unprocessed", "parsed", "extracted", "deduped", "enriched"}


_PHASE_TASK_DEF = {
    "phase1": f"{ENV_NAME}-wwii-phase1-parse",
    "phase2": f"{ENV_NAME}-wwii-phase2-extract",
    "phase3": f"{ENV_NAME}-wwii-phase3-enrich",
}


def _table():
    return boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)


def _claim_doc(table, cache_key: str, current_status: str) -> bool:
    """Atomically move a READY doc to 'running' (claim it for dispatch). Returns
    True if this invocation won the claim — a concurrent drain that already claimed
    it fails the condition and is skipped, so a doc is never double-dispatched."""
    import time as _t

    try:
        table.update_item(
            Key={"cache_key": cache_key},
            UpdateExpression="SET #s = :run, claimed_at = :t",
            ConditionExpression="#s = :cur",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":run": _RUNNING,
                ":cur": current_status,
                ":t": int(_t.time()),
            },
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def enumerate_pending(_event, _context):
    """Return {count, items:[{doc_id, book, phase, task_def}]} for dispatch.

    Scans doc lifecycle records (`doc#{id}`) for READY entries (not running, not
    terminal), ATOMICALLY CLAIMS each (status -> running) so a concurrent/looping
    drain never re-dispatches a doc already being worked, and returns the claimed
    work items carrying their routed task def + phase (§7, §8). The ECS task
    advances the doc on completion (Option 1 — SFN owns lifecycle).
    """
    table = _table()

    # SINGLE scan: snapshot the READY (dispatchable) docs ONCE into memory. We
    # guard on this exact snapshot and then claim ONLY from it — so the set we may
    # dispatch can never exceed the set the guard approved (closes the TOCTOU where
    # a second scan could pick up docs that became READY after the count), and we
    # avoid a second full-table scan (RCU/latency). Nothing is claimed yet.
    candidates = []
    kwargs = {
        "FilterExpression": "begins_with(cache_key, :p)",
        "ExpressionAttributeValues": {":p": "doc#"},
    }
    while True:
        resp = table.scan(**kwargs)
        candidates.extend(
            it for it in resp.get("Items", []) if it.get("status", "") in _READY
        )
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek

    # Shared run-scope safeguard over the DYNAMODB-QUEUED work set (the
    # authoritative AWS work count — NOT an S3 file count). This guards against an
    # UNINTENDED whole-backlog fan-out; it is NOT a concurrency cap. Parallelism
    # (how many batches run at once) is governed separately by clamp_pool /
    # MaxConcurrency. A large INTENTIONAL drain — including many batches in
    # parallel — is supported: declare DISPATCH_EXPECTED_COUNT = the backlog size.
    # The guard only aborts a large backlog that was NOT declared, and because we
    # claim strictly from the guarded snapshot, the dispatched set is bounded by
    # the approved count. Fail-loud but structured (never raise in the SFN task,
    # nothing claimed yet): on violation return 0 items. Not overridable.
    from src.utils.run_scope_guard import (
        LARGE_RUN_CEILING,
        ScopeMismatchError,
        enforce_scope,
    )

    _declared = os.environ.get("DISPATCH_EXPECTED_COUNT")
    try:
        _expected = int(_declared) if _declared not in (None, "") else None
    except ValueError:
        logger.error(
            "DISPATCH ABORTED: DISPATCH_EXPECTED_COUNT=%r is not an integer", _declared
        )
        return {
            "count": 0,
            "items": [],
            "aborted": True,
            "reason": "invalid DISPATCH_EXPECTED_COUNT (not an integer)",
        }
    try:
        enforce_scope(
            actual=len(candidates),
            expected=_expected,
            label="dispatcher queued docs",
            logger=logger,
        )
    except ScopeMismatchError as e:
        logger.error("DISPATCH ABORTED by scope guard: %s", e)
        return {"count": 0, "items": [], "aborted": True, "reason": str(e)}

    # A sanctioned LARGE run (explicitly declared above the ceiling) must not be
    # silent — alert so a declared whole-backlog drain is always auditable.
    if _expected is not None and len(candidates) > LARGE_RUN_CEILING:
        _notify_large_dispatch(len(candidates))

    # Scope approved — claim ONLY from the guarded snapshot, atomically
    # (status -> running). A doc a concurrent drain already claimed fails the
    # conditional and is skipped (no double-dispatch); the dispatched set is a
    # SUBSET of the approved snapshot, never a superset.
    items = []
    for it in candidates:
        if not _claim_doc(table, it["cache_key"], it.get("status", "")):
            continue
        phase = it.get("next_phase", "phase1")
        items.append(
            {
                "doc_id": it["cache_key"].split("#", 1)[-1],
                "book": it.get("book", ""),
                "phase": phase,
                "task_def": _PHASE_TASK_DEF.get(phase, _PHASE_TASK_DEF["phase1"]),
            }
        )
    # FIFO by doc_id (arrival/enumeration order); stable + predictable (§8).
    items.sort(key=lambda x: x["doc_id"])
    logger.info("enumerate_pending: %d dispatchable docs", len(items))
    return {"count": len(items), "items": items}


def clamp_pool(event, _context):
    """Return {effective, pool_min, pool_max, quota_cap} — the Map MaxConcurrency.

    effective = clamp(pool_max, quota-derived cap) with a floor of pool_min (§5.0).
    Reads pool_min/pool_max from the event (dispatcher passes config), queries the
    live Fargate vCPU quota, and never returns a value that would exceed it.
    """
    pool_min = int(event.get("pool_min", 2))
    pool_max = int(event.get("pool_max", 8))
    quota_cap = pool_max
    try:
        sq = boto3.client("service-quotas", region_name=REGION)
        resp = sq.get_service_quota(
            ServiceCode="fargate", QuotaCode=_FARGATE_VCPU_QUOTA
        )
        vcpu = int(resp["Quota"]["Value"])
        quota_cap = max(vcpu // max(_PER_TASK_VCPU, 1), 1)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Fargate vCPU quota lookup failed (%s) — using pool_max", e)
    effective = max(min(pool_max, quota_cap), pool_min)
    # §5.0: warn as concurrency approaches the quota ceiling so the operator can
    # request an increase BEFORE it becomes the bottleneck (a warning, not a fail).
    if quota_cap and pool_max >= 0.8 * quota_cap:
        msg = (
            f"Pool sizing near Fargate vCPU quota ceiling: desired pool_max={pool_max} "
            f"is >=80% of the quota-derived cap={quota_cap} (per-task vCPU={_PER_TASK_VCPU}). "
            f"Effective concurrency capped at {effective}. Request a Fargate vCPU quota "
            f"increase ({_FARGATE_VCPU_QUOTA}) to raise throughput."
        )
        logger.warning(msg)
        _notify_quota_ceiling(msg)
    logger.info(
        "clamp_pool: effective=%d (min=%d max=%d quota_cap=%d)",
        effective,
        pool_min,
        pool_max,
        quota_cap,
    )
    return {
        "effective": effective,
        "pool_min": pool_min,
        "pool_max": pool_max,
        "quota_cap": quota_cap,
    }


def _notify_quota_ceiling(message: str) -> None:
    """Best-effort SNS notification when concurrency nears the quota ceiling (§5.0)."""
    topic = os.getenv("NOTIFICATION_TOPIC_ARN", "")
    if not topic:
        return
    try:
        boto3.client("sns", region_name=REGION).publish(
            TopicArn=topic,
            Subject="WWII Pipeline: concurrency near quota ceiling",
            Message=message,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("quota-ceiling notify failed: %s", e)


def _notify_large_dispatch(count: int) -> None:
    """Best-effort SNS alert when a DECLARED large dispatch (above the scope
    ceiling) is approved — so a sanctioned whole-backlog drain is never silent
    (security review 2026-10-12). Counts only; no secrets/PII."""
    topic = os.getenv("NOTIFICATION_TOPIC_ARN", "")
    if not topic:
        return
    try:
        boto3.client("sns", region_name=REGION).publish(
            TopicArn=topic,
            Subject="WWII Pipeline: large dispatch approved",
            Message=(
                f"Scope guard APPROVED a declared large dispatch of {count} docs "
                f"(DISPATCH_EXPECTED_COUNT matched the ready backlog). This is a "
                f"sanctioned whole-backlog/large drain; alerting for auditability."
            ),
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("large-dispatch notify failed: %s", e)


def human_gate(event, _context):
    """Store or resume a review-gate task token (waitForTaskToken, §17.2).

    action=store: persist {gate#{doc_id}: token} so the review UI can resume it.
    action=resume: send SendTaskSuccess for a stored token (called by the UI/event).
    While parked, the branch holds no NAT lease → contributes 0 to demand (§4).
    """
    action = event.get("action", "store")
    doc_id = event.get("doc_id", "")
    if action == "store":
        token = event.get("task_token", "")
        _table().put_item(
            Item={
                "cache_key": f"gate#{doc_id}",
                "task_token": token,
                "status": "parked",
            }
        )
        logger.info("human_gate: parked %s", doc_id)
        return {"parked": doc_id}
    if action == "resume":
        resp = _table().get_item(Key={"cache_key": f"gate#{doc_id}"})
        token = resp.get("Item", {}).get("task_token", "")
        if token:
            boto3.client("stepfunctions", region_name=REGION).send_task_success(
                taskToken=token, output=event.get("output", "{}")
            )
            _table().delete_item(Key={"cache_key": f"gate#{doc_id}"})
            logger.info("human_gate: resumed %s", doc_id)
            return {"resumed": doc_id}
        logger.warning("human_gate: no token for %s", doc_id)
        return {"error": "no token", "doc_id": doc_id}
    return {"error": f"unknown action {action}"}
