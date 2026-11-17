"""DynamoDB-backed job queue for batch API submissions."""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)


@dataclass
class BatchJob:
    """A submitted batch job awaiting completion."""

    batch_id: str
    phase: str  # "phase2" or "phase3"
    book: str  # e.g. "CrossChannelAttack"
    batch_name: str
    submitted_at: int  # unix timestamp
    status: str = "pending"  # pending, complete, failed, retrieved
    completed_at: int = 0
    request_count: int = 0
    # PHASE2_CHAPTERS of the run that submitted it: a resumed run (retrieve task) keeps the scope
    chapters: str = ""


def _get_table():
    """Get DynamoDB table resource."""
    import os

    table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
    from src.utils.config import get_aws_region

    return boto3.resource("dynamodb", region_name=get_aws_region()).Table(table_name)


def enqueue_job(job: BatchJob) -> None:
    """Add a batch job to the queue. A Phase 2 job carries its run's chapter scope
    (``PHASE2_CHAPTERS``) so the task that resumes it processes the same chapters."""
    import os

    chapters = job.chapters or (
        os.environ.get("PHASE2_CHAPTERS", "") if job.phase == "phase2" else ""
    )
    table = _get_table()
    table.put_item(
        Item={
            "cache_key": f"batch_job#{job.batch_id}",
            "batch_id": job.batch_id,
            "phase": job.phase,
            "book": job.book,
            "batch_name": job.batch_name,
            "submitted_at": job.submitted_at,
            "status": job.status,
            "completed_at": job.completed_at,
            "request_count": job.request_count,
            "ttl": job.submitted_at + 30 * 86400,
            **({"chapters": chapters} if chapters else {}),
        }
    )
    logger.info("Enqueued batch job %s (%s/%s)", job.batch_id, job.phase, job.book)


def get_active_jobs() -> List[BatchJob]:
    """Get all jobs with status 'pending'."""
    table = _get_table()
    resp = table.scan(
        FilterExpression="begins_with(cache_key, :prefix) AND #s = :status",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":prefix": "batch_job#", ":status": "pending"},
    )
    jobs = []
    for item in resp.get("Items", []):
        jobs.append(
            BatchJob(
                batch_id=item["batch_id"],
                phase=item["phase"],
                book=item["book"],
                batch_name=item.get("batch_name", ""),
                submitted_at=int(item["submitted_at"]),
                status=item["status"],
                completed_at=int(item.get("completed_at", 0)),
                request_count=int(item.get("request_count", 0)),
                chapters=item.get("chapters", ""),
            )
        )
    return jobs


def update_job_status(batch_id: str, status: str) -> None:
    """Update a job's status."""
    table = _get_table()
    update_expr = "SET #s = :status"
    expr_values: Dict = {":status": status}
    if status in ("complete", "failed"):
        update_expr += ", completed_at = :ts"
        expr_values[":ts"] = int(time.time())
    table.update_item(
        Key={"cache_key": f"batch_job#{batch_id}"},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=expr_values,
    )
    logger.info("Updated job %s → %s", batch_id, status)


def get_job(batch_id: str) -> Optional[BatchJob]:
    """Get a specific job by batch_id."""
    table = _get_table()
    resp = table.get_item(Key={"cache_key": f"batch_job#{batch_id}"})
    item = resp.get("Item")
    if not item:
        return None
    return BatchJob(
        batch_id=item["batch_id"],
        phase=item["phase"],
        book=item["book"],
        batch_name=item.get("batch_name", ""),
        submitted_at=int(item["submitted_at"]),
        status=item["status"],
        completed_at=int(item.get("completed_at", 0)),
        request_count=int(item.get("request_count", 0)),
        chapters=item.get("chapters", ""),
    )


def remove_job(batch_id: str) -> None:
    """Remove a job from the queue."""
    table = _get_table()
    table.delete_item(Key={"cache_key": f"batch_job#{batch_id}"})
    logger.info("Removed job %s", batch_id)


# A batch a running task is waiting for (it polls up to batch.in_task_wait_minutes). The poller
# leaves it alone while the task is alive; one older than IN_TASK_ADOPT_SECONDS (the wait plus a
# margin) belongs to a task that died during its wait (spot reclaim), and the poller adopts it as
# pending - else the batch is answered and paid for but never retrieved.
IN_TASK = "in_task"


def record_in_task(batch_id: str, batch_name: str, request_count: int) -> None:
    """Record a just-submitted Phase 2 batch as ``in_task`` (ECS only; best-effort - the hand-off
    and in-task retrieve update it)."""
    import os

    phase = os.environ.get("PIPELINE_PHASE", "")
    in_ecs = bool(
        os.environ.get("ECS_CONTAINER_METADATA_URI_V4") or os.environ.get("ECS_TASK_ID")
    )
    if not (in_ecs and phase.startswith("phase2") and batch_id):
        return
    try:
        enqueue_job(
            BatchJob(
                batch_id=batch_id,
                phase="phase2",
                book=os.environ.get("BOOK_NAME", "unknown") or "unknown",
                batch_name=batch_name,
                submitted_at=int(time.time()),
                status=IN_TASK,
                request_count=request_count,
            )
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - never a batch failure: it is already submitted
        logger.warning(
            "OBSERV batch.in_task_record_failed batch=%s err=%s", batch_id, e
        )


def mark_retrieved_in_task(batch_id: str) -> None:
    """The task got the answers itself: the ``in_task`` record is done."""
    mark_in_task(batch_id, "retrieved")


def mark_in_task(batch_id: str, status: str) -> None:
    """Settle this task's ``in_task`` record (best-effort; Phase 2 in ECS only, and only an
    EXISTING row - an update must never create a bare row for another phase's batch)."""
    import os

    if not batch_id:
        return
    if not (
        os.environ.get("ECS_CONTAINER_METADATA_URI_V4") or os.environ.get("ECS_TASK_ID")
    ):
        return
    if not os.environ.get("PIPELINE_PHASE", "").startswith("phase2"):
        return
    try:
        _get_table().update_item(
            Key={"cache_key": f"batch_job#{batch_id}"},
            UpdateExpression="SET #s = :status",
            ConditionExpression="attribute_exists(cache_key)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": status},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV batch.in_task_update_failed batch=%s err=%s", batch_id, e
        )
