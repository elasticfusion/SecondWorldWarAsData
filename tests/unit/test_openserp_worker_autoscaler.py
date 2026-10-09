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


def _lambda_mock(ready=True):
    """A lambda client whose invoke returns nat-manager's {'status':'ready'} (or not)."""
    import io
    import json

    lam = MagicMock()
    payload = json.dumps({"status": "ready" if ready else "not_ready"}).encode()
    lam.invoke.return_value = {"Payload": io.BytesIO(payload)}
    return lam


def _run(backlog, current, nat_ready=True):
    sqs, ecs = _mocks(backlog, current)
    lam = _lambda_mock(ready=nat_ready)

    def _client(name, **k):
        return {"sqs": sqs, "ecs": ecs, "lambda": lam}[name]

    with patch("boto3.client", side_effect=_client):
        out = a.handler({}, None)
    return out, ecs, lam


def test_scale_up_from_zero_ensures_nat_and_scales():
    out, ecs, lam = _run(backlog=60, current=0)  # 60/25 -> ceil = 3
    assert out["action"] == "scaled" and out["to"] == 3 and out["from"] == 0
    # NAT create was invoked synchronously before scaling.
    assert lam.invoke.called
    assert b'"action": "create"' in lam.invoke.call_args_list[0].kwargs["Payload"]
    assert ecs.update_service.call_args.kwargs["desiredCount"] == 3


def test_scale_up_deferred_when_nat_not_ready():
    sqs, ecs = _mocks(backlog=60, current=0)
    with patch(
        "boto3.client",
        side_effect=lambda n, **k: {"sqs": sqs, "ecs": ecs, "lambda": MagicMock()}[n],
    ):
        with patch.object(a, "_ensure_nat_ready", return_value=False):
            out = a.handler({}, None)
    assert out["action"] == "deferred" and out["reason"] == "nat not ready"
    ecs.update_service.assert_not_called()  # don't place tasks before NAT is ready


def test_scale_down_no_nat_invoke():
    out, ecs, lam = _run(backlog=20, current=4)  # 20 -> desired 1
    assert out["action"] == "scaled" and out["to"] == 1 and out["from"] == 4
    lam.invoke.assert_not_called()  # scale-DOWN never touches NAT
    assert ecs.update_service.call_args.kwargs["desiredCount"] == 1


def test_scale_to_zero_when_empty():
    out, ecs, lam = _run(backlog=0, current=2)
    assert out["action"] == "scaled" and out["to"] == 0
    lam.invoke.assert_not_called()
    assert ecs.update_service.call_args.kwargs["desiredCount"] == 0


def test_unchanged_no_update():
    out, ecs, _lam = _run(backlog=10, current=1)  # 10 -> desired 1 == current
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
