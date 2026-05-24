"""Lambda handler: poll Grok batch API for pending jobs.

Triggered by EventBridge schedule (every 15-30 min).
Checks all pending batch jobs, updates status, and triggers
ECS retrieve task when a batch completes.
"""

import json
import logging
import os

import boto3
import requests

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

GROK_API_BASE = "https://api.x.ai/v1"
ENV_NAME = os.getenv("ENV_NAME", "dev")
REGION = os.getenv("AWS_REGION", "us-east-1")
CLUSTER = os.getenv("ECS_CLUSTER", f"{ENV_NAME}-wwii-pipeline")
CACHE_TABLE = os.getenv("CACHE_TABLE", f"{ENV_NAME}-wwii-api-cache")
NOTIFICATION_TOPIC_ARN = os.getenv("NOTIFICATION_TOPIC_ARN", "")


def handler(event, _context):
    """Poll pending batch jobs OR trigger a new submit-only ECS task.

    EventBridge schedule → polls pending jobs.
    Direct invoke with {"action": "submit", "phase": "phase3", "book": "..."} → launches submit task.
    """
    action = event.get("action", "poll")

    if action == "submit":
        return _handle_submit(event)

    # Default: poll pending jobs
    api_key = _get_api_key()
    if not api_key:
        logger.error("No GROK_API_KEY available")
        return {"error": "no api key"}

    jobs = _get_pending_jobs()
    if not jobs:
        logger.info("No pending batch jobs")
        return {"checked": 0}

    logger.info("Checking %d pending batch job(s)", len(jobs))
    results = {"checked": len(jobs), "complete": 0, "pending": 0, "failed": 0}

    for job in jobs:
        status = _check_batch_status(
            api_key, job["batch_id"], int(job.get("submitted_at", 0))
        )
        if status == "complete":
            if _mark_complete(job["batch_id"]):
                _trigger_retrieve(job)
                results["complete"] += 1
            else:
                results["pending"] += 1  # another invocation handling it
        elif status == "failed":
            _mark_failed(job["batch_id"])
            _notify(f"Batch {job['batch_id']} FAILED ({job.get('book', '?')})")
            results["failed"] += 1
        else:
            results["pending"] += 1

    logger.info("Results: %s", results)
    return results


def _handle_submit(event: dict) -> dict:
    """Create networking and launch ECS submit-only task."""
    phase = event.get("phase", "phase3")
    book = event.get("book", "unknown")
    phase_script = "phase2_extract.py" if phase == "phase2" else "phase3_enrich_data.py"

    # Create networking first
    try:
        lam = boto3.client("lambda", region_name=REGION)
        lam.invoke(
            FunctionName=f"{ENV_NAME}-wwii-nat-manager",
            InvocationType="RequestResponse",
            Payload=json.dumps({"action": "create"}).encode(),
        )
        logger.info("Networking created for submit task")
    except Exception as e:
        logger.error("Failed to create networking: %s", e)
        return {"error": f"networking failed: {e}"}

    # Launch ECS task with --submit-only
    ecs = boto3.client("ecs", region_name=REGION)
    task_def = (
        f"{ENV_NAME}-wwii-phase2-extract"
        if phase == "phase2"
        else f"{ENV_NAME}-wwii-phase3-enrich"
    )

    try:
        resp = ecs.run_task(
            cluster=CLUSTER,
            taskDefinition=task_def,
            count=1,
            capacityProviderStrategy=[
                {"capacityProvider": "FARGATE_SPOT", "weight": 4, "base": 0},
                {"capacityProvider": "FARGATE", "weight": 1, "base": 0},
            ],
            networkConfiguration=_get_network_config(),
            overrides={
                "containerOverrides": [
                    {
                        "name": "pipeline",
                        "command": [
                            "--submit-only",
                            phase_script,
                        ],
                        "environment": [
                            {"name": "BOOK_NAME", "value": book},
                        ],
                    }
                ]
            },
        )
        task_arn = resp["tasks"][0]["taskArn"] if resp.get("tasks") else "unknown"
        logger.info("Started submit task %s (%s/%s)", task_arn, phase, book)
        return {"task_arn": task_arn, "phase": phase, "book": book}
    except Exception as e:
        logger.error("Failed to start submit task: %s", e)
        return {"error": str(e)}


def _get_api_key() -> str:
    """Get Grok API key from Secrets Manager."""
    secret_id = os.getenv("SECRETS_ID", "")
    if not secret_id:
        return os.getenv("GROK_API_KEY", "")
    sm = boto3.client("secretsmanager", region_name=REGION)
    return sm.get_secret_value(SecretId=secret_id)["SecretString"]


def _get_pending_jobs() -> list:
    """Scan DynamoDB for pending batch jobs."""
    table = boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)
    items = []
    kwargs = {
        "FilterExpression": "begins_with(cache_key, :prefix) AND #s = :status",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":prefix": "batch_job#", ":status": "pending"},
    }
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def _check_batch_status(api_key: str, batch_id: str, submitted_at: int = 0) -> str:
    """Check batch status via Grok API. Returns 'complete', 'pending', or 'failed'."""
    import time

    try:
        resp = requests.get(
            f"{GROK_API_BASE}/batches/{batch_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        state = batch.get("state", {})
        total = state.get("num_requests", 0)
        success = state.get("num_success", 0)
        error = state.get("num_error", 0)

        logger.info(
            "Batch %s: %d/%d (%d ok, %d err)",
            batch_id[:12],
            success + error,
            total,
            success,
            error,
        )

        if total > 0 and success + error >= total:
            # All errors = failed; otherwise complete
            if error >= total:
                return "failed"
            return "complete"

        # Timeout: if >24h old and has some successes, treat as complete
        if submitted_at and success > 0:
            age_hours = (time.time() - submitted_at) / 3600
            if age_hours > 24:
                logger.warning(
                    "Batch %s stuck for %.0fh (%d/%d), treating as complete",
                    batch_id[:12],
                    age_hours,
                    success + error,
                    total,
                )
                return "complete"

        return "pending"
    except Exception as e:
        logger.error("Failed to check batch %s: %s", batch_id, e)
        return "pending"  # don't mark failed on transient errors


def _mark_complete(batch_id: str) -> bool:
    """Atomically update job status to complete. Returns True if this invocation claimed it."""
    import time

    table = boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)
    try:
        table.update_item(
            Key={"cache_key": f"batch_job#{batch_id}"},
            UpdateExpression="SET #s = :new_status, completed_at = :ts",
            ConditionExpression="#s = :expected",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":new_status": "complete",
                ":expected": "pending",
                ":ts": int(time.time()),
            },
        )
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        logger.info("Job %s already claimed by another invocation", batch_id)
        return False


def _mark_failed(batch_id: str) -> None:
    """Update job status to failed."""
    import time

    table = boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)
    table.update_item(
        Key={"cache_key": f"batch_job#{batch_id}"},
        UpdateExpression="SET #s = :status, completed_at = :ts",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":status": "failed", ":ts": int(time.time())},
    )


def _trigger_retrieve(job: dict) -> None:
    """Start ECS task in retrieve-only mode for the completed batch."""
    batch_id = job["batch_id"]
    phase = job.get("phase", "phase3")
    phase_script = "phase2_extract.py" if phase == "phase2" else "phase3_enrich_data.py"

    # First, ensure networking is up (synchronous — must be ready before ECS launch)
    try:
        lam = boto3.client("lambda", region_name=REGION)
        lam.invoke(
            FunctionName=f"{ENV_NAME}-wwii-nat-manager",
            InvocationType="RequestResponse",
            Payload=json.dumps({"action": "create"}).encode(),
        )
        logger.info("Networking ready for retrieve task")
    except Exception as e:
        logger.warning("Failed to create networking: %s", e)

    # Launch ECS task with --retrieve-only
    ecs = boto3.client("ecs", region_name=REGION)
    task_def = (
        f"{ENV_NAME}-wwii-phase2-extract"
        if phase == "phase2"
        else f"{ENV_NAME}-wwii-phase3-enrich"
    )

    try:
        resp = ecs.run_task(
            cluster=CLUSTER,
            taskDefinition=task_def,
            count=1,
            capacityProviderStrategy=[
                {"capacityProvider": "FARGATE_SPOT", "weight": 4, "base": 0},
                {"capacityProvider": "FARGATE", "weight": 1, "base": 0},
            ],
            networkConfiguration=_get_network_config(),
            overrides={
                "containerOverrides": [
                    {
                        "name": "pipeline",
                        "command": [
                            "--retrieve-only",
                            batch_id,
                            phase_script,
                        ],
                        "environment": [
                            {"name": "BOOK_NAME", "value": job.get("book", "unknown")},
                        ],
                    }
                ]
            },
        )
        task_arn = resp["tasks"][0]["taskArn"] if resp.get("tasks") else "unknown"
        logger.info("Started retrieve task %s for batch %s", task_arn, batch_id)
        _notify(
            f"Batch {batch_id} complete ({job.get('book', '?')}, "
            f"{job.get('request_count', '?')} reqs). Retrieve task started."
        )
    except Exception as e:
        logger.error("Failed to start retrieve task for %s: %s", batch_id, e)
        _notify(f"Batch {batch_id} complete but FAILED to start retrieve: {e}")
        # Reset to pending so poller retries on next cycle
        try:
            table = boto3.resource("dynamodb", region_name=REGION).Table(CACHE_TABLE)
            table.update_item(
                Key={"cache_key": f"batch_job#{batch_id}"},
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "pending"},
            )
            logger.info("Reset job %s to pending for retry", batch_id)
        except Exception:
            pass


def _get_network_config() -> dict:
    """Build Fargate network configuration from environment."""
    subnets = [
        s.strip() for s in os.getenv("PRIVATE_SUBNET_IDS", "").split(",") if s.strip()
    ]
    sg = os.getenv("SECURITY_GROUP_ID", "")
    return {
        "awsvpcConfiguration": {
            "subnets": subnets,
            "securityGroups": [sg] if sg else [],
            "assignPublicIp": "DISABLED",
        }
    }


def _notify(message: str) -> None:
    """Send SNS notification."""
    if not NOTIFICATION_TOPIC_ARN:
        return
    try:
        boto3.client("sns", region_name=REGION).publish(
            TopicArn=NOTIFICATION_TOPIC_ARN,
            Subject="WWII Pipeline: Batch Status",
            Message=message,
        )
    except Exception as e:
        logger.warning("Failed to notify: %s", e)
