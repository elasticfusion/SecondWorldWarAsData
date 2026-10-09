"""Lambda: autoscale the OpenSERP SQS worker service on queue depth (SQS scaling, step 5).

EventBridge-scheduled (every few minutes). Reads the OpenSERP work queue depth and sets the
worker service desiredCount to drain it:

    backlog = visible + in-flight messages
    desired = 0 if backlog == 0
              else clamp(ceil(backlog / MESSAGES_PER_WORKER), 1, MAX_POOL)

Scale-to-zero is native (empty queue -> 0). On scale-UP from zero, NAT is ensured first (the
worker needs ECR/S3/Grok/search egress; the nat-manager demand check now counts the worker, so
NAT stays up while draining — see nat_manager._nat_demand_present). The worker itself idle-exits
after an empty-poll streak, so this controller mostly ADDS capacity under load and lets workers
drain + self-exit; it only forces desired=0 when the queue is confirmed empty, to release the
service cleanly.
"""

import logging
import math
import os

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

ENV_NAME = os.getenv("ENV_NAME", "dev")
MAX_POOL = max(1, int(os.getenv("WORKER_MAX_POOL", "4")))
MESSAGES_PER_WORKER = max(1, int(os.getenv("MESSAGES_PER_WORKER", "25")))


def _queue_url(sqs) -> str:
    return sqs.get_queue_url(QueueName=f"{ENV_NAME}-wwii-openserp-work")["QueueUrl"]


def _backlog(sqs, queue_url: str) -> int:
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]
    return int(attrs.get("ApproximateNumberOfMessages", 0)) + int(
        attrs.get("ApproximateNumberOfMessagesNotVisible", 0)
    )


def _desired_for(backlog: int) -> int:
    if backlog <= 0:
        return 0
    return max(1, min(MAX_POOL, math.ceil(backlog / MESSAGES_PER_WORKER)))


def _ensure_nat_ready(timeout_s: int = 45) -> bool:
    """Bring NAT up and CONFIRM it is ready before workers are scaled up from zero.

    Returns True once nat-manager reports ready (NAT + required endpoints available), else
    False. We invoke SYNCHRONOUSLY (RequestResponse) and poll action=verify, because an async
    nudge + immediate scale raced ECS task placement ahead of NAT availability -> ECR pull
    i/o timeout (the step-4 bug). If NAT isn't ready within the budget we DON'T scale this
    cycle; the next scheduled tick retries (the backlog is still there)."""
    import json
    import time

    import boto3

    region = os.getenv("AWS_REGION", "us-east-1")
    lam = boto3.client("lambda", region_name=region)
    fn = f"{ENV_NAME}-wwii-nat-manager"
    try:
        resp = lam.invoke(
            FunctionName=fn,
            InvocationType="RequestResponse",
            Payload=b'{"action": "create"}',
        )
        body = json.loads(resp["Payload"].read() or b"{}")
        if body.get("status") == "ready":
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning("NAT create invoke failed: %s", e)
        return False
    # create returned not_ready (endpoints/NAT still coming up): poll verify briefly.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(10)
        try:
            v = lam.invoke(
                FunctionName=fn,
                InvocationType="RequestResponse",
                Payload=b'{"action": "verify"}',
            )
            if json.loads(v["Payload"].read() or b"{}").get("ready"):
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("NAT verify invoke failed: %s", e)
            return False
    logger.warning(
        "NAT not ready within %ds — deferring scale-up to next tick", timeout_s
    )
    return False


def handler(event, _context):
    """Scale the worker service to match the current queue backlog."""
    import boto3

    region = os.getenv("AWS_REGION", "us-east-1")
    sqs = boto3.client("sqs", region_name=region)
    ecs = boto3.client("ecs", region_name=region)
    cluster = f"{ENV_NAME}-wwii-pipeline"
    service = f"{ENV_NAME}-wwii-openserp-worker"

    try:
        queue_url = _queue_url(sqs)
    except Exception as e:  # noqa: BLE001
        logger.warning("work queue not found (%s) — nothing to scale", e)
        return {"action": "none", "reason": "no queue"}

    try:
        backlog = _backlog(sqs, queue_url)
        svc = ecs.describe_services(cluster=cluster, services=[service]).get(
            "services", []
        )
        current = svc[0].get("desiredCount", 0) if svc else 0
    except Exception as e:  # noqa: BLE001 - transient SQS/ECS error; next tick retries
        logger.error("autoscaler read failed: %s", e)
        return {"action": "error", "reason": str(e)}

    desired = _desired_for(backlog)

    if desired == current:
        logger.info("backlog=%d desired=%d (unchanged)", backlog, desired)
        return {"action": "none", "backlog": backlog, "desired": desired}

    # Scaling UP from zero: NAT must be CONFIRMED ready before the first task is placed,
    # else ECS schedules the task ahead of NAT and the ECR pull times out. If NAT isn't
    # ready yet, defer scaling to the next tick (backlog persists).
    if desired > current and current == 0:
        if not _ensure_nat_ready():
            return {"action": "deferred", "reason": "nat not ready", "backlog": backlog}

    try:
        ecs.update_service(cluster=cluster, service=service, desiredCount=desired)
    except Exception as e:  # noqa: BLE001
        logger.error("autoscaler update_service failed: %s", e)
        return {"action": "error", "reason": str(e)}
    logger.info("backlog=%d scaled worker %d -> %d", backlog, current, desired)
    return {"action": "scaled", "backlog": backlog, "from": current, "to": desired}
