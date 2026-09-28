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
_IN_FLIGHT = {
    "expanding",
    "routed",
    "ocr",
    "parsed",
    "extracted",
    "deduped",
    "enriched",
}

_PHASE_TASK_DEF = {
    "phase1": f"{ENV_NAME}-wwii-phase1-parse",
    "phase2": f"{ENV_NAME}-wwii-phase2-extract",
    "phase3": f"{ENV_NAME}-wwii-phase3-enrich",
}


def _table():
    return boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)


def enumerate_pending(_event, _context):
    """Return {count, items:[{doc_id, book, phase, task_def}]} for dispatch.

    Scans doc lifecycle records (`doc#{id}`) for entries not done and not already
    in-flight. FIFO order (§8). Each item is a heterogeneous work item carrying
    its routed task def (§7). Only NOT-in-flight docs are returned so a restart
    never re-dispatches running work (idempotent, §8).
    """
    table = _table()
    items = []
    kwargs = {
        "FilterExpression": "begins_with(cache_key, :p)",
        "ExpressionAttributeValues": {":p": "doc#"},
    }
    while True:
        resp = table.scan(**kwargs)
        for it in resp.get("Items", []):
            status = it.get("status", "")
            if status in _LIFECYCLE_DONE or status in _IN_FLIGHT:
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
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
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
