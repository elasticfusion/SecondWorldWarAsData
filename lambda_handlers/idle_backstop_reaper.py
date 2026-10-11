"""Idle-infra backstop reaper (data-quality/cost TODO #1).

Defense-in-depth against a future bug in `openserp_manager`/`nat_manager` silently burning NAT +
Fargate for days (the 8-day leak found 2026-10-08). This runs HOURLY and is INDEPENDENT of the
10-minute manager: even if the manager's teardown predicate regresses, this reaper force-tears-down
on a CONFIRMED stall.

Design (per the TODO spec):
- Progress is measured by PROGRESS, not "a task exists": the count of objects under ``output/`` in
  the data bucket (extraction/enrichment grows it). A legitimately long batch that keeps advancing
  moves this number and is NEVER killed.
- Two-strike debounce: a single no-progress hour does nothing. Only TWO CONSECUTIVE hourly
  observations with (a) NAT up, (b) no pipeline lock/lease held, and (c) no output growth trip the
  teardown. The strike counter + last count are stored in DynamoDB (``reaper#state``) across runs.
- On the second strike: force the OpenSERP service to 0 + invoke nat-manager delete, ALERT the
  operator via the ``{env}-wwii-phase2-complete`` SNS topic, then reset the strike counter.
- Fail-safe: any error logs + exits without tearing down (never destroy on uncertainty).
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("S3_BUCKET", f"{ENV_NAME}-wwii-data-pipeline")
CACHE_TABLE = os.environ.get("CACHE_TABLE", f"{ENV_NAME}-wwii-api-cache")
TOPIC_ARN = os.environ.get(
    "NOTIFICATION_TOPIC_ARN",
    "",
)
_STATE_KEY = "reaper#state"
_STRIKES_TO_REAP = 2  # two consecutive no-progress hours = confirmed stall


def _output_object_count(s3) -> int:
    """Progress signal: number of objects under output/ (paginated count; cheap KeyCount sum)."""
    total = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix="output/"):
        total += page.get("KeyCount", 0)
    return total


def _nat_up(ec2) -> bool:
    resp = ec2.describe_nat_gateways(
        Filters=[
            {"Name": "tag:Name", "Values": [f"{ENV_NAME}-nat"]},
            {"Name": "state", "Values": ["available", "pending"]},
        ]
    )
    return len(resp.get("NatGateways", [])) > 0


def _lock_or_lease_held(table) -> bool:
    """A held pipeline lock or LIVE NAT lease means real work — never reap.

    CRITICAL: do NOT use scan Limit — in DynamoDB, Limit caps items evaluated BEFORE the filter, and
    this shared cache table holds tens of thousands of non-lock rows, so a small Limit would almost
    never see a lock#/nat#lease# row (false 'no lock'). Paginate fully. Leases are TTL-filtered
    (DynamoDB TTL deletion lags by minutes–hours, so a crashed task's expired lease must NOT count
    as live — otherwise the backstop would never reap the exact crash-leak it exists for).
    """
    import time as _time

    now = int(_time.time())
    paginator_kwargs = {
        "ProjectionExpression": "cache_key, #ttl",
        "ExpressionAttributeNames": {"#ttl": "ttl"},
    }
    # Locks (short TTL, deleted on task exit) — any present means work in flight.
    start_key = None
    while True:
        kw = dict(
            FilterExpression="begins_with(cache_key, :p)",
            ExpressionAttributeValues={":p": "lock#"},
            **paginator_kwargs,
        )
        if start_key:
            kw["ExclusiveStartKey"] = start_key
        resp = table.scan(**kw)
        if resp.get("Items"):
            return True
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    # NAT leases — count only NON-expired (ttl missing or in the future).
    start_key = None
    while True:
        kw = dict(
            FilterExpression="begins_with(cache_key, :p)",
            ExpressionAttributeValues={":p": "nat#lease#"},
            **paginator_kwargs,
        )
        if start_key:
            kw["ExclusiveStartKey"] = start_key
        resp = table.scan(**kw)
        for item in resp.get("Items", []):
            ttl = item.get("ttl")
            if ttl is None or int(ttl) > now:
                return True
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return False


def _load_state(table):
    item = table.get_item(Key={"cache_key": _STATE_KEY}).get("Item", {})
    return (
        int(item.get("strikes", 0)),
        int(item.get("last_count", -1)),
        str(item.get("updated_at", "")),
    )


def _save_state(table, strikes: int, last_count: int) -> None:
    table.put_item(
        Item={
            "cache_key": _STATE_KEY,
            "strikes": strikes,
            "last_count": last_count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _alert(message: str) -> None:
    if not TOPIC_ARN:
        logger.error("reaper: NOTIFICATION_TOPIC_ARN unset — cannot alert: %s", message)
        return
    try:
        import boto3

        boto3.client("sns", region_name=REGION).publish(
            TopicArn=TOPIC_ARN,
            Subject="WWII Pipeline: COST — idle-infra backstop reaper fired",
            Message=message,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("reaper: alert publish failed: %s", e)


def _force_teardown(ecs, cluster, service) -> None:
    import boto3

    try:
        ecs.update_service(cluster=cluster, service=service, desiredCount=0)
        logger.warning("reaper: forced OpenSERP service to 0")
    except Exception as e:  # noqa: BLE001
        logger.error("reaper: scale-to-zero failed: %s", e)
    try:
        boto3.client("lambda", region_name=REGION).invoke(
            FunctionName=f"{ENV_NAME}-wwii-nat-manager",
            InvocationType="Event",
            Payload=b'{"action": "delete"}',
        )
        logger.warning("reaper: nat-manager delete invoked")
    except Exception as e:  # noqa: BLE001
        logger.error("reaper: nat delete invoke failed: %s", e)


def handler(event, _context=None):
    """Hourly backstop. Fail-safe: never tears down on uncertainty/error."""
    try:
        import boto3

        ec2 = boto3.client("ec2", region_name=REGION)
        s3 = boto3.client("s3", region_name=REGION)
        ecs = boto3.client("ecs", region_name=REGION)
        table = boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)

        # If NAT is already down there is no cost leak to reap — reset + exit.
        if not _nat_up(ec2):
            _save_state(table, 0, -1)
            return {"action": "none", "reason": "nat already down"}

        # Real work in flight (lock/lease) -> reset strikes, never reap. Reset baseline to -1
        # (first post-lock hour is only strike 1) rather than paying for a full S3 count here.
        if _lock_or_lease_held(table):
            _save_state(table, 0, -1)
            return {"action": "none", "reason": "lock/lease held — work in flight"}

        count = _output_object_count(s3)
        strikes, last_count, last_updated = _load_state(table)

        progressed = last_count >= 0 and count != last_count
        if progressed:
            _save_state(table, 0, count)
            return {"action": "none", "reason": "progress observed", "count": count}

        # Time-bound the strike: EventBridge is at-least-once and manual invokes happen, so only let
        # a strike count toward the "2 consecutive HOURS" rule if >= ~50 min elapsed since the last
        # observation. Otherwise record the count but do NOT advance the strike (anti-double-count).
        import datetime as _dt

        if strikes > 0 and last_updated:
            try:
                age_s = (
                    _dt.datetime.now(timezone.utc)
                    - _dt.datetime.fromisoformat(last_updated)
                ).total_seconds()
            except ValueError:
                age_s = 0  # malformed timestamp -> treat as "too soon", do NOT advance (fail-safe)
            if age_s < 50 * 60:
                _save_state(
                    table, strikes, count
                )  # keeps updated_at fresh, strike unchanged
                return {
                    "action": "none",
                    "reason": "too soon since last strike (anti-double-count)",
                    "strikes": strikes,
                }

        strikes += 1
        if strikes < _STRIKES_TO_REAP:
            _save_state(table, strikes, count)
            logger.info(
                "reaper: no-progress strike %d/%d (count=%d, NAT up, no lock)",
                strikes,
                _STRIKES_TO_REAP,
                count,
            )
            return {"action": "none", "reason": "first strike", "strikes": strikes}

        # Second consecutive no-progress hour with NAT up + no lock = confirmed stall.
        cluster = f"{ENV_NAME}-wwii-pipeline"
        service = f"{ENV_NAME}-wwii-openserp"
        _force_teardown(ecs, cluster, service)
        _alert(
            "The idle-infra backstop reaper force-tore-down networking: NAT was up with NO pipeline "
            f"lock/lease and NO output growth across {_STRIKES_TO_REAP} consecutive hourly checks "
            f"(output object count stuck at {count}). The 10-minute manager should have caught this "
            "— investigate why it did not (possible teardown-predicate regression)."
        )
        _save_state(table, 0, count)
        logger.warning("reaper: FORCE TEARDOWN fired (confirmed 2h stall)")
        return {"action": "force_teardown", "strikes": strikes, "count": count}
    except (
        Exception
    ) as e:  # noqa: BLE001 - a reaper bug must never crash loudly or reap wrongly
        logger.error("reaper: skipped on error (no teardown): %s", e)
        return {"action": "none", "reason": f"error: {e}"}
