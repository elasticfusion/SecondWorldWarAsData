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


def test_scheduled_lock_check_clears_stale(dynamodb_table):
    from lambda_handlers.trigger_handler import handler

    # Seed a stale lock
    table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-wwii-api-cache")
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
        "Records": [{
            "body": json.dumps({
                "TopicArn": "arn:aws:sns:us-east-1:123:test-wwii-content-uploaded",
                "Message": json.dumps({
                    "Records": [{"s3": {"object": {"key": "content/Book/ch1/file.md"}}}]
                }),
            })
        }]
    }
    topics, keys = _extract_records(event)
    assert "test-wwii-content-uploaded" in topics
    assert "content/Book/ch1/file.md" in keys


def test_queue_pending(dynamodb_table):
    from lambda_handlers.trigger_handler import _queue_pending

    _queue_pending(["content/Book/ch1.md", "content/Book/ch2.md"])

    table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-wwii-api-cache")
    item = table.get_item(Key={"cache_key": "pending#content"})["Item"]
    assert len(item["keys"]) == 2
