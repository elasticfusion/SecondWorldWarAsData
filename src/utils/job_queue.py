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


def _get_table():
    """Get DynamoDB table resource."""
    import os

    table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    return boto3.resource("dynamodb", region_name=region).Table(table_name)


def enqueue_job(job: BatchJob) -> None:
    """Add a batch job to the queue."""
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
            "ttl": job.submitted_at + 7 * 86400,
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
    )


def remove_job(batch_id: str) -> None:
    """Remove a job from the queue."""
    table = _get_table()
    table.delete_item(Key={"cache_key": f"batch_job#{batch_id}"})
    logger.info("Removed job %s", batch_id)
