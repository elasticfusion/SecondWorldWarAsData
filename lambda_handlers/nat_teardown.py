"""NAT/endpoint TEARDOWN decider — the SOLE owner of networking teardown.

Lifecycle model (owner directive 2026-10-15):
  * Phases 1/2/3 NEVER tear down NAT/endpoints — they may only REQUEST start via the
    nat_manager Lambda (action=create).
  * nat_manager owns bring-up (action=create) + the actual delete primitive
    (action=delete), but DELETE is invoked ONLY from here.
  * THIS Lambda is the only teardown decider. It fires on a 4-HOUR cron. On each run:
      - If the DynamoDB queue has work being processed OR waiting to be processed, OR any
        container is running an ongoing process  ->  do NOTHING.
      - If a container is on with ongoing work, re-check every 10 MINUTES (a one-shot
        self-rescheduled EventBridge Scheduler invoke) until idle — don't wait for the
        next 4h tick.
      - Only when the queue is empty AND no container is processing  ->  tear down NAT +
        endpoints (nat_manager action=delete).

Demand = `nat_manager._nat_demand_present()` (single source of truth): live nat#lease#
entries, running non-openserp ECS pipeline tasks, in-flight OCR Batch jobs, and
pending/ready Grok batch_job# rows. Fail-safe: on ANY uncertainty, keep NAT (never tear
down on error).
"""

import logging
import os

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
RECHECK_MINUTES = int(os.getenv("NAT_TEARDOWN_RECHECK_MINUTES", "10"))
_RECHECK_SCHEDULE = f"{ENV_NAME}-wwii-nat-teardown-recheck"


def _nat_present(ec2) -> bool:
    """True if a NAT gateway exists (available/pending) — nothing to tear down if not."""
    resp = ec2.describe_nat_gateways(
        Filters=[
            {"Name": "tag:Name", "Values": [f"{ENV_NAME}-nat"]},
            {"Name": "state", "Values": ["available", "pending"]},
        ]
    )
    return bool(resp.get("NatGateways", []))


STATUS_PORT = int(os.getenv("STATUS_PORT", "8077"))
STATUS_HTTP_TIMEOUT = int(os.getenv("STATUS_HTTP_TIMEOUT", "3"))


def _is_private_ipv4(ip: str) -> bool:
    """True only for RFC1918 private IPv4 (task ENIs live in the VPC CIDR). Guards the
    /status poll so we never urlopen a public/unexpected address."""
    import ipaddress

    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _running_pipeline_task_ips() -> list:
    """Private IPs of RUNNING pipeline tasks (exclude the openserp browser SERVICE, which
    runs no entrypoint/status API). Returns [(task_id, ip)]."""
    import boto3

    ecs = boto3.client("ecs", region_name=REGION)
    cluster = f"{ENV_NAME}-wwii-pipeline"
    browser_group = f"service:{ENV_NAME}-wwii-openserp"
    arns = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING").get("taskArns", [])
    if not arns:
        return []
    out = []
    for t in ecs.describe_tasks(cluster=cluster, tasks=arns).get("tasks", []):
        if (t.get("group", "") or "") == browser_group:
            continue
        task_id = (t.get("taskArn", "") or "").split("/")[-1]
        ip = ""
        for att in t.get("attachments", []):
            for d in att.get("details", []):
                if d.get("name") == "privateIPv4Address":
                    ip = d.get("value", "")
        if ip:
            out.append((task_id, ip))
    return out


def _containers_busy() -> bool:
    """Poll each RUNNING pipeline container's /status HTTP API (option 3 — a REAL
    on-container 'ongoing task?' endpoint). Returns True (keep NAT) if ANY container
    reports busy OR is UNREACHABLE (fail-safe: an unreachable container might be mid-work
    with a wedged status thread — never tear down on uncertainty). False only when every
    running pipeline container answered and all reported idle. No running tasks -> False
    (the DynamoDB demand check still covers queue work with no container, e.g. a pending
    Grok batch)."""
    import json as _json
    import urllib.request

    try:
        tasks = _running_pipeline_task_ips()
    except Exception as e:  # noqa: BLE001 - fail safe
        logger.warning(
            "container status: task discovery failed (%s) — assuming busy", e
        )
        return True
    for task_id, ip in tasks:
        if not _is_private_ipv4(ip):
            logger.warning(
                "container status: %s has non-private IP %s — assuming busy",
                task_id,
                ip,
            )
            return True
        # URL is our own fixed http:// to a validated RFC1918 task IP — not
        # attacker-controlled and not a user-supplied scheme (bandit B310 N/A here).
        url = f"http://{ip}:{STATUS_PORT}/status"
        try:
            with urllib.request.urlopen(
                url, timeout=STATUS_HTTP_TIMEOUT
            ) as r:  # nosec B310
                data = _json.loads(r.read().decode("utf-8"))
            if data.get("busy"):
                logger.info(
                    "container status: %s busy (idle_seconds=%s) — keeping NAT",
                    task_id,
                    data.get("idle_seconds"),
                )
                return True
            logger.info("container status: %s idle", task_id)
        except Exception as e:  # noqa: BLE001 - unreachable == uncertain == keep NAT
            logger.warning(
                "container status: %s (%s) unreachable (%s) — assuming busy",
                task_id,
                ip,
                e,
            )
            return True
    return False


def _schedule_recheck(account_id: str) -> None:
    """Self-schedule a ONE-SHOT recheck in RECHECK_MINUTES via EventBridge Scheduler, so
    while a container is still processing we poll at 10-min cadence (not the 4h cron) and
    tear down promptly once idle. ActionAfterCompletion=DELETE cleans the one-shot up.
    ``account_id`` comes from the Lambda context ARN (no STS call -> no sts VPC endpoint).
    """
    import datetime

    import boto3

    account = account_id
    fn_arn = f"arn:aws:lambda:{REGION}:{account}:function:{ENV_NAME}-wwii-nat-teardown"
    role_arn = os.environ.get(
        "SCHEDULER_ROLE_ARN",
        f"arn:aws:iam::{account}:role/{ENV_NAME}-wwii-scheduler-role",
    )
    run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=RECHECK_MINUTES
    )
    scheduler = boto3.client("scheduler", region_name=REGION)
    kwargs = dict(
        Name=_RECHECK_SCHEDULE,
        ScheduleExpression=f"at({run_at.strftime('%Y-%m-%dT%H:%M:%S')})",
        ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": fn_arn,
            "RoleArn": role_arn,
            "Input": '{"source": "recheck"}',
        },
        ActionAfterCompletion="DELETE",
    )
    try:
        scheduler.create_schedule(**kwargs)
        logger.info("Scheduled NAT-teardown recheck in %d min", RECHECK_MINUTES)
    except scheduler.exceptions.ConflictException:
        # A one-shot with this name already exists — most often the just-FIRED recheck
        # still mid-cleanup (ActionAfterCompletion=DELETE ordering vs this re-invocation
        # is not guaranteed). UPDATE it to the new run time so the 10-min chain re-arms
        # DETERMINISTICALLY (leaving it would break the chain → next eval only at the 4h
        # cron). If it vanished between create and update, create once more.
        try:
            scheduler.update_schedule(**kwargs)
            logger.info("Re-armed NAT-teardown recheck (+%d min)", RECHECK_MINUTES)
        except scheduler.exceptions.ResourceNotFoundException:
            try:
                scheduler.create_schedule(**kwargs)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to re-create NAT-teardown recheck: %s", e)
    except (
        Exception
    ) as e:  # noqa: BLE001 - scheduling failure must not crash the decider
        logger.warning("Failed to schedule NAT-teardown recheck: %s", e)


def _teardown() -> dict:
    """Invoke nat_manager action=delete (the only place delete is invoked)."""
    import boto3

    lam = boto3.client("lambda", region_name=REGION)
    resp = lam.invoke(
        FunctionName=f"{ENV_NAME}-wwii-nat-manager",
        InvocationType="RequestResponse",
        Payload=b'{"action": "delete"}',
    )
    logger.info("NAT-teardown: queue empty + no container — invoked nat_manager delete")
    return {"action": "delete", "nat_manager_status": resp.get("StatusCode")}


def _ddb_networking_signal() -> bool:
    """Cheap NAT-state probe over the FREE DynamoDB gateway endpoint (always reachable, no
    interface endpoint / NAT needed). Returns True if ANY of these suggest NAT is (or
    should be) UP: a live nat#lease#, a pending/ready batch_job#, or a pipeline lock#.

    This is the gate that lets the decider run endpoint-free when NAT is down: the control-
    plane interface endpoints (ec2/ecs/lambda/scheduler) are created WITH NAT and deleted
    WITH NAT (owner directive), so when NAT is down those calls would time out. If this DDB
    signal is False (nothing up), we exit 'nothing to do' WITHOUT any endpoint-dependent
    call. Fail-safe: on error return True (proceed to the authoritative EC2 check)."""
    import time as _t

    import boto3

    now = int(_t.time())
    try:
        tbl = boto3.resource("dynamodb", region_name=REGION).Table(
            os.getenv("CACHE_TABLE", f"{ENV_NAME}-wwii-api-cache")
        )
        for prefix, live in (
            ("nat#lease#", True),
            ("batch_job#", False),
            ("lock#", False),
        ):
            resp = tbl.scan(
                FilterExpression="begins_with(cache_key, :p)",
                ExpressionAttributeValues={":p": prefix},
                ProjectionExpression="cache_key, #t, #s",
                ExpressionAttributeNames={"#t": "ttl", "#s": "status"},
            )
            for item in resp.get("Items", []):
                if prefix == "nat#lease#":
                    ttl = item.get("ttl")
                    if ttl is None or int(ttl) > now:
                        return True
                elif prefix == "batch_job#":
                    if item.get("status") in ("pending", "ready"):
                        return True
                else:  # lock#
                    return True
        return False
    except Exception as e:  # noqa: BLE001 - fail safe: proceed to the EC2 check
        logger.warning("DDB networking signal failed (%s) — proceeding to EC2 check", e)
        return True


def handler(event, _context=None):
    """4h cron (and 10-min self-recheck) teardown decider. Fail-safe: never tears down on
    uncertainty/error."""
    try:
        import boto3

        from lambda_handlers.nat_manager import _nat_demand_present

        # Account id from the Lambda context ARN (arn:aws:lambda:region:ACCOUNT:function:..)
        # — avoids an STS call (so no sts VPC endpoint needed); env fallback for local tests.
        account_id = ""
        try:
            account_id = (getattr(_context, "invoked_function_arn", "") or "").split(
                ":"
            )[4]
        except (IndexError, AttributeError):
            account_id = ""
        account_id = account_id or os.getenv("AWS_ACCOUNT_ID", "")

        # Cheap FREE-endpoint pre-gate: if DynamoDB shows no leases/batches/locks, NAT is
        # down/idle and its dynamic control-plane endpoints are gone — exit WITHOUT an EC2
        # call (which would otherwise time out ~90s with the endpoints torn down).
        if not _ddb_networking_signal():
            logger.info(
                "NAT-teardown: DDB shows no networking demand/lease — nothing to do"
            )
            return {"action": "none", "reason": "no networking signal (ddb)"}

        ec2 = boto3.client("ec2", region_name=REGION)
        if not _nat_present(ec2):
            logger.info("NAT-teardown: no NAT present — nothing to do")
            return {"action": "none", "reason": "nat already down"}

        if _nat_demand_present():
            # Queue has work (processing/waiting) OR a container is running an ongoing
            # process -> do NOT tear down; recheck in 10 min rather than wait 4h.
            logger.info(
                "NAT-teardown: demand present (queue work / running container) — "
                "rechecking in %d min",
                RECHECK_MINUTES,
            )
            _schedule_recheck(account_id)
            return {"action": "recheck", "reason": "nat demand present"}

        # Authoritative on-container check (option 3): poll each running pipeline
        # container's /status HTTP API. A busy/unreachable container keeps NAT up.
        if _containers_busy():
            logger.info(
                "NAT-teardown: a container reports an ongoing task — rechecking in %d min",
                RECHECK_MINUTES,
            )
            _schedule_recheck(account_id)
            return {"action": "recheck", "reason": "container busy (status API)"}

        # Queue empty AND no container processing -> tear down.
        return _teardown()
    except (
        Exception
    ) as e:  # noqa: BLE001 - a decider bug must never crash or wrongly reap
        logger.error("NAT-teardown: skipped on error (no teardown): %s", e)
        return {"action": "none", "reason": f"error: {e}"}
