"""Tests for the OCR SPOT controller (Option B: spot-retry / 48h on-demand cap)."""

import os
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("NOTIFICATION_TOPIC_ARN", "")

from lambda_handlers import ocr_spot_controller as ctl


@pytest.fixture
def table():
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1")
        c.create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(
            "test-wwii-api-cache"
        )


def test_resubmit_to_ondemand_moves_once(table):
    batch = MagicMock()
    batch.describe_jobs.return_value = {
        "jobs": [
            {
                "container": {
                    "command": ["s3://in.pdf", "s3://out/", "--page-range", "1-50"]
                }
            }
        ]
    }
    batch.submit_job.return_value = {"jobId": "od-1"}
    job = {"jobName": "chandra-Big-p1-50", "jobId": "spot-1"}
    with patch.object(ctl, "_batch", return_value=batch):
        ctl._resubmit_to_ondemand(job)
        ctl._resubmit_to_ondemand(job)  # second call must be a no-op (already moved)
    # submitted to on-demand exactly once + terminated the spot job
    assert batch.submit_job.call_count == 1
    assert batch.submit_job.call_args.kwargs["jobQueue"].endswith("-ondemand")
    batch.terminate_job.assert_called_once()
    rec = table.get_item(Key={"cache_key": "ocrctl#chandra-Big-p1-50"})["Item"]
    assert rec["ondemand_job_id"] == "od-1"


def test_route_spot_starved_only_old_runnable(table):
    now_ms = int(time.time() * 1000)
    batch = MagicMock()
    # one old (2h) RUNNABLE, one fresh (1min)
    with (
        patch.object(ctl, "_batch", return_value=batch),
        patch.object(
            ctl,
            "_list_jobs",
            return_value=[
                {
                    "jobName": "old",
                    "jobId": "s1",
                    "createdAt": now_ms - 2 * 3600 * 1000,
                },
                {"jobName": "fresh", "jobId": "s2", "createdAt": now_ms - 60 * 1000},
            ],
        ),
        patch.object(ctl, "_resubmit_to_ondemand") as resub,
    ):
        n = ctl._route_spot_starved()
    assert n == 1  # only the >1h one routed
    resub.assert_called_once()
    assert resub.call_args[0][0]["jobName"] == "old"


def test_enforce_ondemand_cap_terminates_past_48h(table):
    now = int(time.time())
    table.put_item(
        Item={
            "cache_key": "ocrctl#stuck",
            "ondemand_job_id": "od-9",
            "ondemand_started_at": now - 49 * 3600,  # 49h ago
        }
    )
    table.put_item(
        Item={
            "cache_key": "ocrctl#ok",
            "ondemand_job_id": "od-10",
            "ondemand_started_at": now - 2 * 3600,  # 2h ago
        }
    )
    batch = MagicMock()
    with patch.object(ctl, "_batch", return_value=batch), patch.object(ctl, "_notify"):
        capped = ctl._enforce_ondemand_cap()
    assert capped == 1
    batch.terminate_job.assert_called_once_with(
        jobId="od-9", reason="48h on-demand cap (§2)"
    )


def test_warn_near_quota_fires_at_80pct(table):
    # quota 64 vCPU; 16 running spot jobs * 4 = 64 vCPU = 100% >= 80% -> warn
    with (
        patch.object(ctl, "_spot_quota_vcpus", return_value=64),
        patch.object(
            ctl, "_list_jobs", return_value=[{"jobName": f"j{i}"} for i in range(16)]
        ),
        patch.object(ctl, "_notify") as notify,
    ):
        ctl._warn_if_near_quota()
    notify.assert_called_once()


def test_warn_near_quota_silent_below_80pct(table):
    with (
        patch.object(ctl, "_spot_quota_vcpus", return_value=64),
        patch.object(
            ctl, "_list_jobs", return_value=[{"jobName": "j"}]  # 1 job * 4 = 4 vCPU
        ),
        patch.object(ctl, "_notify") as notify,
    ):
        ctl._warn_if_near_quota()
    notify.assert_not_called()
