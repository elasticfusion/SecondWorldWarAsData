"""Unit tests for src/utils/job_queue.py."""

import os
import time

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from src.utils.job_queue import (
    BatchJob,
    enqueue_job,
    get_active_jobs,
    get_job,
    remove_job,
    update_job_status,
)


@pytest.fixture
def dynamodb_table():
    """Create a mock DynamoDB table."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def _make_job(batch_id="batch-123", phase="phase3", book="TestBook"):
    return BatchJob(
        batch_id=batch_id,
        phase=phase,
        book=book,
        batch_name=f"{phase}-{book}",
        submitted_at=int(time.time()),
        status="pending",
        request_count=100,
    )


def test_enqueue_and_get(dynamodb_table):
    job = _make_job()
    enqueue_job(job)
    result = get_job("batch-123")
    assert result is not None
    assert result.batch_id == "batch-123"
    assert result.phase == "phase3"
    assert result.book == "TestBook"
    assert result.status == "pending"
    assert result.request_count == 100


def test_get_active_jobs(dynamodb_table):
    enqueue_job(_make_job("batch-1"))
    enqueue_job(_make_job("batch-2"))
    jobs = get_active_jobs()
    assert len(jobs) == 2
    assert {j.batch_id for j in jobs} == {"batch-1", "batch-2"}


def test_get_active_jobs_excludes_complete(dynamodb_table):
    enqueue_job(_make_job("batch-1"))
    enqueue_job(_make_job("batch-2"))
    update_job_status("batch-1", "complete")
    jobs = get_active_jobs()
    assert len(jobs) == 1
    assert jobs[0].batch_id == "batch-2"


def test_update_job_status(dynamodb_table):
    enqueue_job(_make_job())
    update_job_status("batch-123", "complete")
    job = get_job("batch-123")
    assert job.status == "complete"
    assert job.completed_at > 0


def test_update_job_status_failed(dynamodb_table):
    enqueue_job(_make_job())
    update_job_status("batch-123", "failed")
    job = get_job("batch-123")
    assert job.status == "failed"
    assert job.completed_at > 0


def test_remove_job(dynamodb_table):
    enqueue_job(_make_job())
    remove_job("batch-123")
    assert get_job("batch-123") is None


def test_get_job_not_found(dynamodb_table):
    assert get_job("nonexistent") is None


def test_ttl_is_set(dynamodb_table):
    enqueue_job(_make_job())
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    resp = table.get_item(Key={"cache_key": "batch_job#batch-123"})
    item = resp["Item"]
    assert "ttl" in item
    assert int(item["ttl"]) > int(time.time())
