"""Unit tests for lambda_handlers/trigger_handler.py."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("ECS_CLUSTER", "test-wwii-pipeline")
os.environ.setdefault("PRIVATE_SUBNET_IDS", "subnet-abc123")
os.environ.setdefault("SECURITY_GROUP_ID", "sg-abc123")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("NOTIFICATION_TOPIC_ARN", "")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("NAT_MANAGER_FN", "test-wwii-nat-manager")
os.environ.setdefault("NETWORKING_STACK", "test-wwii-networking")
os.environ.setdefault("PHASE1_TASK_DEF", "test-wwii-phase1-parse")
os.environ.setdefault("PHASE2_TASK_DEF", "test-wwii-phase2-extract")
os.environ.setdefault("PHASE3_TASK_DEF", "test-wwii-phase3-enrich")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


@pytest.fixture
def dynamodb_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


# --- G1: per-book lock key + PROVISIONING-aware stale check (idle-race fix) ---


def test_lock_key_serial_is_singleton(dynamodb_table):
    from lambda_handlers import trigger_handler as th

    with patch.object(th, "MULTI_DOC_ENABLED", False):
        assert th._lock_key("test-wwii-phase2-extract", "B460") == (
            "lock#test-wwii-phase2-extract"
        )


def test_lock_key_multi_doc_is_per_book(dynamodb_table):
    from lambda_handlers import trigger_handler as th

    with patch.object(th, "MULTI_DOC_ENABLED", True):
        assert th._lock_key("test-wwii-phase2-extract", "B460") == (
            "lock#test-wwii-phase2-extract#B460"
        )


def test_lock_key_multi_doc_no_book_falls_back_singleton(dynamodb_table):
    from lambda_handlers import trigger_handler as th

    with patch.object(th, "MULTI_DOC_ENABLED", True):
        assert th._lock_key("test-wwii-phase1-parse", "") == (
            "lock#test-wwii-phase1-parse"
        )


def test_run_task_does_not_clear_lock_when_task_provisioning(dynamodb_table):
    """G1 race: a held lock with a PROVISIONING (not-yet-RUNNING) task must NOT be
    treated as stale — the 2nd invocation must defer, not clear+relaunch."""
    from lambda_handlers import trigger_handler as th
    import boto3 as _b

    table = _b.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    # Pre-existing lock (first doc holds it)
    table.put_item(
        Item={
            "cache_key": "lock#test-wwii-phase1-parse",
            "book": "all",
            "response": "1",
        }
    )

    def fake_list_tasks(cluster, family, desiredStatus):
        # No RUNNING, but a PROVISIONING task exists (NAT cold-start window)
        return (
            {"taskArns": ["arn:task/x"]}
            if desiredStatus == "PROVISIONING"
            else {"taskArns": []}
        )

    with (
        patch.object(th.ecs, "list_tasks", side_effect=fake_list_tasks),
        patch.object(th.ecs, "run_task") as run_task,
        patch.object(th, "_wait_for_networking"),
    ):
        th._run_task(th.PHASE1_TASK_DEF, "test")
    # Must NOT have launched a second task (lock respected, task is starting)
    run_task.assert_not_called()
    # Lock still present (not cleared)
    assert table.get_item(Key={"cache_key": "lock#test-wwii-phase1-parse"}).get("Item")


def test_scheduled_lock_check_clears_stale(dynamodb_table):
    from lambda_handlers.trigger_handler import handler

    # Seed a stale lock
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    table.put_item(Item={"cache_key": "lock#test-wwii-phase1-parse", "response": "123"})

    with patch("lambda_handlers.trigger_handler.ecs") as mock_ecs:
        mock_ecs.list_tasks.return_value = {"taskArns": []}
        with patch("lambda_handlers.trigger_handler.s3") as mock_s3:
            mock_s3.get_object.side_effect = Exception("no file")
            result = handler({"source": "scheduled"}, None)

    assert result == {"action": "lock_check_complete"}
    # Lock should be cleared
    resp = table.get_item(Key={"cache_key": "lock#test-wwii-phase1-parse"})
    assert "Item" not in resp


def test_extract_records_from_sqs():
    from lambda_handlers.trigger_handler import _extract_records

    event = {
        "Records": [
            {
                "body": json.dumps(
                    {
                        "TopicArn": "arn:aws:sns:us-east-1:123:test-wwii-content-uploaded",
                        "Message": json.dumps(
                            {
                                "Records": [
                                    {
                                        "s3": {
                                            "object": {
                                                "key": "content/Book/ch1/file.md"
                                            }
                                        }
                                    }
                                ]
                            }
                        ),
                    }
                )
            }
        ]
    }
    topics, keys = _extract_records(event)
    assert "test-wwii-content-uploaded" in topics
    assert "content/Book/ch1/file.md" in keys


def test_queue_pending(dynamodb_table):
    from lambda_handlers.trigger_handler import _queue_pending

    _queue_pending(["content/Book/ch1.md", "content/Book/ch2.md"])

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    item = table.get_item(Key={"cache_key": "pending#content"})["Item"]
    assert len(item["keys"]) == 2


def test_phase_complete_event_dispatches_to_drive_next(dynamodb_table):
    """A phase-complete invoke must route to _drive_next_phase (event-driven chain)."""
    from lambda_handlers import trigger_handler as th

    with patch.object(th, "_reconcile_pending", return_value=["2"]) as rec:
        out = th.handler({"source": "phase-complete", "phase": "1"}, None)
    assert out["action"] == "drive_next_phase"
    assert out["completed"] == "1"
    rec.assert_called_once()


def test_reconcile_launches_phase1_when_content_parked_and_idle(dynamodb_table):
    """Parked content + idle cluster => launch Phase 1 (the B460-strand fix)."""
    from lambda_handlers import trigger_handler as th

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    table.put_item(
        Item={
            "cache_key": "pending#content",
            "keys": ["contentrepository/B460/B460.md"],
        }
    )
    with (
        patch.object(th.ecs, "list_tasks", return_value={"taskArns": []}),
        patch.object(th, "_run_task") as run,
    ):
        launched = th._reconcile_pending(reason="test")
    assert launched == ["1"]
    run.assert_called_once()
    # book parsed from contentrepository/{book}/... => B460
    assert run.call_args.kwargs.get("book_name") == "B460" or "B460" in str(
        run.call_args
    )


def test_reconcile_defers_when_busy(dynamodb_table):
    """Cluster busy => do NOT launch, leave parked content for later."""
    from lambda_handlers import trigger_handler as th

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    table.put_item(
        Item={"cache_key": "pending#content", "keys": ["contentrepository/X/X.md"]}
    )
    with (
        patch.object(
            th.ecs, "list_tasks", return_value={"taskArns": ["arn:task/running"]}
        ),
        patch.object(th, "_run_task") as run,
    ):
        launched = th._reconcile_pending(reason="test")
    assert launched == []
    run.assert_not_called()


def test_reconcile_noop_when_nothing_parked(dynamodb_table):
    from lambda_handlers import trigger_handler as th

    with (
        patch.object(th.ecs, "list_tasks", return_value={"taskArns": []}),
        patch.object(th, "_get_pending_books", return_value=[]),
        patch.object(th, "_get_pending_books_for_enrich", return_value=[]),
        patch.object(th, "_run_task") as run,
    ):
        launched = th._reconcile_pending(reason="test")
    assert launched == []
    run.assert_not_called()


# --- G4: OCR intake idempotency (§8) — deny duplicate submissions at the front door ---


def test_submit_ocr_first_claims_and_submits(dynamodb_table):
    from lambda_handlers import trigger_handler as th
    from unittest.mock import MagicMock
    import boto3 as _b

    batch = MagicMock()
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "_ocr_chunks", return_value=[""]),
    ):
        ok = th._submit_ocr("contentrepository/NARA/B-Series/B 400-499/B460.pdf")
    assert ok is True
    batch.submit_job.assert_called_once()
    table = _b.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    assert table.get_item(Key={"cache_key": "ocr#B460"}).get("Item")


def test_submit_ocr_duplicate_is_denied(dynamodb_table):
    from lambda_handlers import trigger_handler as th
    from unittest.mock import MagicMock

    batch = MagicMock()
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "_ocr_chunks", return_value=[""]),
    ):
        first = th._submit_ocr("contentrepository/B460/B460.pdf")
        second = th._submit_ocr("contentrepository/B460/B460.pdf")  # duplicate event
    assert first is True
    assert second is False  # denied at intake
    assert batch.submit_job.call_count == 1  # only ONE GPU job submitted


def test_submit_ocr_releases_claim_on_submit_failure(dynamodb_table):
    """A failed submit must release the claim so a genuine retry isn't blocked."""
    from lambda_handlers import trigger_handler as th
    from unittest.mock import MagicMock
    import boto3 as _b

    batch = MagicMock()
    batch.submit_job.side_effect = RuntimeError("Batch down")
    with (
        patch.object(th, "_batch_client", return_value=batch),
        patch.object(th, "_ocr_chunks", return_value=[""]),
    ):
        ok = th._submit_ocr("contentrepository/B460/B460.pdf")
    assert ok is False
    table = _b.resource("dynamodb", region_name="us-east-1").Table(
        "test-wwii-api-cache"
    )
    assert table.get_item(Key={"cache_key": "ocr#B460"}).get("Item") is None
