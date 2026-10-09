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
MAX_POOL = int(os.getenv("WORKER_MAX_POOL", "4"))
MESSAGES_PER_WORKER = int(os.getenv("MESSAGES_PER_WORKER", "25"))


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


def _ensure_nat() -> None:
    """Bring NAT up before scaling workers up from zero (fire-and-forget create)."""
    import boto3

    try:
        boto3.client("lambda", region_name=os.getenv("AWS_REGION", "us-east-1")).invoke(
            FunctionName=f"{ENV_NAME}-wwii-nat-manager",
            InvocationType="Event",  # async; create is idempotent + safe to call repeatedly
            Payload=b'{"action": "create"}',
        )
    except Exception as e:  # noqa: BLE001 - never block scaling on the NAT nudge
        logger.warning("NAT ensure invoke failed: %s", e)


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

    backlog = _backlog(sqs, queue_url)
    desired = _desired_for(backlog)

    svc = ecs.describe_services(cluster=cluster, services=[service]).get("services", [])
    current = svc[0].get("desiredCount", 0) if svc else 0

    if desired == current:
        logger.info("backlog=%d desired=%d (unchanged)", backlog, desired)
        return {"action": "none", "backlog": backlog, "desired": desired}

    # Bring NAT up BEFORE scaling up from zero so the first task can pull from ECR.
    if desired > current and current == 0:
        _ensure_nat()

    ecs.update_service(cluster=cluster, service=service, desiredCount=desired)
    logger.info("backlog=%d scaled worker %d -> %d", backlog, current, desired)
    return {
        "action": "scaled",
        "backlog": backlog,
        "from": current,
        "to": desired,
    }
