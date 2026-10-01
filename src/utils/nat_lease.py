"""Job-aware NAT leases (M3 of CONCURRENCY_AND_NAT_SPEC §4).

Replaces the timing-heuristic NAT teardown (task-count + lock-scan + age windows,
which races a PROVISIONING task) with an explicit, cluster-wide **demand** signal:

    NAT is UP iff any job needs egress OR queued work will need it imminently.

Each network-needing task writes a **lease** (`nat#lease#{task_id}`) with a short
TTL and heartbeats it while alive; it deletes the lease on exit (normal + SIGTERM).
Demand = count of live (non-expired) leases. A crashed/spot-killed task cannot leak
demand forever — its lease expires by TTL. `nat_manager`/`openserp_manager` compute
demand from **live leases + running ECS tasks + pending queues** (ground truth), so
a missed decrement self-heals.

This module is import-safe with no side effects and defaults to the SAFE choice on
any error: "demand present" (never tear NAT down on uncertainty).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Lease key namespace. Demand = count of keys with this prefix that are live.
LEASE_PREFIX = "nat#lease#"

# Default lease TTL (seconds). Must exceed the heartbeat interval by a comfortable
# margin so a slow heartbeat doesn't let the lease expire under a live task.
DEFAULT_LEASE_TTL = int(os.getenv("NAT_LEASE_TTL_SECONDS", "900"))  # 15 min


def _table(region: Optional[str] = None):
    import boto3

    region = region or os.getenv(
        "AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    )
    table_name = os.getenv(
        "CACHE_TABLE", f"{os.getenv('ENV_NAME', 'dev')}-wwii-api-cache"
    )
    return boto3.resource("dynamodb", region_name=region).Table(table_name)


def _task_id() -> str:
    """Stable identifier for this unit of work's lease.

    Prefers the ECS task id (set from container metadata at entrypoint import);
    falls back to hostname+pid for local runs so leases are still unique.
    """
    tid = os.getenv("ECS_TASK_ID", "")
    if tid:
        return tid
    import socket

    return f"{socket.gethostname()}-{os.getpid()}"


def acquire_lease(
    ttl_seconds: int = DEFAULT_LEASE_TTL, task_id: Optional[str] = None
) -> bool:
    """Register this task's NAT demand. Idempotent (re-put refreshes the lease).

    Returns True on success. On error, returns False but never raises — lease
    acquisition must not block the pipeline (demand is also cross-checked against
    running tasks, so a missed lease is recoverable).
    """
    tid = task_id or _task_id()
    try:
        _table().put_item(
            Item={
                "cache_key": f"{LEASE_PREFIX}{tid}",
                "response": str(int(time.time())),
                "ttl": int(time.time()) + ttl_seconds,
            }
        )
        logger.info("Acquired NAT lease: %s%s", LEASE_PREFIX, tid)
        return True
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to acquire NAT lease: %s", e)
        return False


def heartbeat_lease(
    ttl_seconds: int = DEFAULT_LEASE_TTL, task_id: Optional[str] = None
) -> None:
    """Extend this task's lease TTL. Called periodically alongside the S3 sync loop."""
    acquire_lease(ttl_seconds=ttl_seconds, task_id=task_id)


def release_lease(task_id: Optional[str] = None) -> None:
    """Drop this task's NAT demand (normal exit, SIGTERM, or human-gate park)."""
    tid = task_id or _task_id()
    try:
        _table().delete_item(Key={"cache_key": f"{LEASE_PREFIX}{tid}"})
        logger.info("Released NAT lease: %s%s", LEASE_PREFIX, tid)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Failed to release NAT lease: %s", e)


def live_lease_count(region: Optional[str] = None) -> int:
    """Count non-expired NAT leases (cluster-wide demand from leases).

    DynamoDB TTL deletion is eventually consistent (can lag minutes), so filter
    expired leases in-code by their `ttl` rather than trusting deletion latency.
    """
    now = int(time.time())
    count = 0
    try:
        table = _table(region)
        kwargs = {
            "FilterExpression": "begins_with(cache_key, :p)",
            "ExpressionAttributeValues": {":p": LEASE_PREFIX},
            "ProjectionExpression": "cache_key, #t",
            "ExpressionAttributeNames": {"#t": "ttl"},
        }
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items", []):
                ttl = item.get("ttl")
                if ttl is None or int(ttl) > now:
                    count += 1
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Lease count failed: %s — assuming demand present", e)
        return 1  # SAFE: assume demand so NAT is not torn down on error
    return count


def has_nat_demand(
    running_task_count: int = 0,
    pending_queue_depth: int = 0,
    region: Optional[str] = None,
) -> bool:
    """Cluster-wide NAT demand (the §4 invariant, ground-truth cross-checked).

    Demand exists if ANY of: live leases > 0, running (non-openserp) tasks > 0,
    or queued work implies imminent demand. Callers pass the ECS/queue ground
    truth so a dropped lease self-heals.
    """
    if running_task_count > 0 or pending_queue_depth > 0:
        return True
    return live_lease_count(region) > 0
