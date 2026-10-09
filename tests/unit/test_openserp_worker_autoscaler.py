"""Tests for the OpenSERP worker autoscaler lambda (SQS scaling step 5), offline."""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ENV_NAME", "dev")

from lambda_handlers import openserp_worker_autoscaler as a


def test_desired_sizing():
    assert a._desired_for(0) == 0
    assert a._desired_for(1) == 1
    assert a._desired_for(a.MESSAGES_PER_WORKER) == 1
    assert a._desired_for(a.MESSAGES_PER_WORKER + 1) == 2
    # capped at MAX_POOL
    assert a._desired_for(a.MESSAGES_PER_WORKER * (a.MAX_POOL + 5)) == a.MAX_POOL


def _mocks(backlog, current):
    sqs = MagicMock()
    sqs.get_queue_url.return_value = {"QueueUrl": "q"}
    sqs.get_queue_attributes.return_value = {
        "Attributes": {
            "ApproximateNumberOfMessages": str(backlog),
            "ApproximateNumberOfMessagesNotVisible": "0",
        }
    }
    ecs = MagicMock()
    ecs.describe_services.return_value = {"services": [{"desiredCount": current}]}
    return sqs, ecs


def _run(backlog, current):
    sqs, ecs = _mocks(backlog, current)

    def _client(name, **k):
        return {"sqs": sqs, "ecs": ecs, "lambda": MagicMock()}[name]

    with patch("boto3.client", side_effect=_client):
        out = a.handler({}, None)
    return out, ecs


def test_scale_up_from_zero_ensures_nat_and_scales():
    out, ecs = _run(backlog=60, current=0)  # 60/25 -> ceil = 3
    assert out["action"] == "scaled" and out["to"] == 3 and out["from"] == 0
    ecs.update_service.assert_called_once()
    assert ecs.update_service.call_args.kwargs["desiredCount"] == 3


def test_scale_to_zero_when_empty():
    out, ecs = _run(backlog=0, current=2)
    assert out["action"] == "scaled" and out["to"] == 0
    ecs.update_service.assert_called_once()
    assert ecs.update_service.call_args.kwargs["desiredCount"] == 0


def test_unchanged_no_update():
    out, ecs = _run(backlog=10, current=1)  # 10 -> desired 1 == current
    assert out["action"] == "none"
    ecs.update_service.assert_not_called()


def test_missing_queue_is_noop():
    sqs = MagicMock()
    sqs.get_queue_url.side_effect = RuntimeError("not found")
    with patch(
        "boto3.client",
        side_effect=lambda name, **k: {
            "sqs": sqs,
            "ecs": MagicMock(),
            "lambda": MagicMock(),
        }[name],
    ):
        out = a.handler({}, None)
    assert out["action"] == "none" and out["reason"] == "no queue"
