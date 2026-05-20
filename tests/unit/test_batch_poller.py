"""Unit tests for lambda_handlers/batch_poller.py."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("ENV_NAME", "test")
os.environ.setdefault("ECS_CLUSTER", "test-wwii-pipeline")
os.environ.setdefault("PRIVATE_SUBNET_IDS", "subnet-abc123")
os.environ.setdefault("SECURITY_GROUP_ID", "sg-abc123")
os.environ.setdefault("SECRETS_ID", "")
os.environ.setdefault("GROK_API_KEY", "test-key")


@pytest.fixture
def dynamodb_table():
    """Create mock DynamoDB table and seed a pending job."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def _seed_job(batch_id="batch-abc", phase="phase3", book="TestBook"):
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-wwii-api-cache")
    table.put_item(Item={
        "cache_key": f"batch_job#{batch_id}",
        "batch_id": batch_id,
        "phase": phase,
        "book": book,
        "batch_name": f"{phase}-{book}",
        "submitted_at": int(time.time()),
        "status": "pending",
        "request_count": 500,
    })


def test_poll_no_pending_jobs(dynamodb_table):
    from lambda_handlers.batch_poller import handler

    result = handler({}, None)
    assert result == {"checked": 0}


@patch("lambda_handlers.batch_poller.requests.get")
@patch("lambda_handlers.batch_poller._trigger_retrieve")
def test_poll_batch_complete(mock_trigger, mock_get, dynamodb_table):
    from lambda_handlers.batch_poller import handler

    _seed_job()
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"state": {"num_requests": 500, "num_success": 500, "num_error": 0}},
        raise_for_status=lambda: None,
    )

    result = handler({}, None)
    assert result["complete"] == 1
    assert result["pending"] == 0
    mock_trigger.assert_called_once()

    # Verify job marked complete in DynamoDB
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-wwii-api-cache")
    item = table.get_item(Key={"cache_key": "batch_job#batch-abc"})["Item"]
    assert item["status"] == "complete"


@patch("lambda_handlers.batch_poller.requests.get")
def test_poll_batch_still_pending(mock_get, dynamodb_table):
    from lambda_handlers.batch_poller import handler

    _seed_job()
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"state": {"num_requests": 500, "num_success": 200, "num_error": 0}},
        raise_for_status=lambda: None,
    )

    result = handler({}, None)
    assert result["pending"] == 1
    assert result["complete"] == 0


@patch("lambda_handlers.batch_poller.requests.get")
@patch("lambda_handlers.batch_poller._notify")
def test_poll_batch_failed(mock_notify, mock_get, dynamodb_table):
    from lambda_handlers.batch_poller import handler

    _seed_job()
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"state": {"num_requests": 500, "num_success": 0, "num_error": 500}},
        raise_for_status=lambda: None,
    )

    result = handler({}, None)
    assert result["failed"] == 1
    mock_notify.assert_called_once()

    table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-wwii-api-cache")
    item = table.get_item(Key={"cache_key": "batch_job#batch-abc"})["Item"]
    assert item["status"] == "failed"


@patch("lambda_handlers.batch_poller.requests.get")
def test_poll_api_error_stays_pending(mock_get, dynamodb_table):
    from lambda_handlers.batch_poller import handler

    _seed_job()
    mock_get.side_effect = Exception("connection timeout")

    result = handler({}, None)
    assert result["pending"] == 1

    table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-wwii-api-cache")
    item = table.get_item(Key={"cache_key": "batch_job#batch-abc"})["Item"]
    assert item["status"] == "pending"


@patch("lambda_handlers.batch_poller.boto3.client")
def test_submit_action_creates_networking_and_launches_task(mock_boto_client, dynamodb_table):
    from lambda_handlers.batch_poller import handler

    mock_lambda = MagicMock()
    mock_ecs = MagicMock()
    mock_ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:aws:ecs:us-east-1:123:task/abc"}]}

    def client_factory(service, **kwargs):
        if service == "lambda":
            return mock_lambda
        if service == "ecs":
            return mock_ecs
        return MagicMock()

    mock_boto_client.side_effect = client_factory

    result = handler({"action": "submit", "phase": "phase3", "book": "CrossChannelAttack"}, None)

    # Verify nat_manager invoked
    mock_lambda.invoke.assert_called_once()
    invoke_args = mock_lambda.invoke.call_args
    assert "nat-manager" in invoke_args[1]["FunctionName"]
    assert json.loads(invoke_args[1]["Payload"])["action"] == "create"

    # Verify ECS task launched with --submit-only
    mock_ecs.run_task.assert_called_once()
    overrides = mock_ecs.run_task.call_args[1]["overrides"]
    cmd = overrides["containerOverrides"][0]["command"]
    assert "--submit-only" in cmd
    assert "phase3_enrich_data.py" in cmd

    assert "task_arn" in result


@patch("lambda_handlers.batch_poller.requests.get")
@patch("lambda_handlers.batch_poller._trigger_retrieve")
def test_poll_multiple_jobs(mock_trigger, mock_get, dynamodb_table):
    from lambda_handlers.batch_poller import handler

    _seed_job("batch-1")
    _seed_job("batch-2")

    call_count = [0]

    def mock_response(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # First job complete
            return MagicMock(
                status_code=200,
                json=lambda: {"state": {"num_requests": 500, "num_success": 500, "num_error": 0}},
                raise_for_status=lambda: None,
            )
        else:
            # Second job pending
            return MagicMock(
                status_code=200,
                json=lambda: {"state": {"num_requests": 500, "num_success": 100, "num_error": 0}},
                raise_for_status=lambda: None,
            )

    mock_get.side_effect = mock_response

    result = handler({}, None)
    assert result["checked"] == 2
    assert result["complete"] == 1
    assert result["pending"] == 1
