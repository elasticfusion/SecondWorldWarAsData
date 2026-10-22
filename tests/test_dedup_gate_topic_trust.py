"""§1.0d HIGH regression: dedup_gate_handler must only honor a `dedup_complete` SNS signal from OUR
dedup-complete topic. The signal fans out Phase 3 over the whole corpus (bypassing human dedup
review), so a spoofed / cross-account / misconfigured-subscription record must be dropped.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import lambda_handlers.dedup_gate_handler as g

OUR_ARN = "arn:aws:sns:us-east-1:123456789012:dev-wwii-dedup-complete"


# --- _is_trusted_dedup_topic (pure) ---


def test_trusted_topic_accepts_exact_arn(monkeypatch):
    monkeypatch.setenv("DEDUP_COMPLETE_TOPIC_ARN", OUR_ARN)
    assert g._is_trusted_dedup_topic(OUR_ARN) is True


@pytest.mark.parametrize(
    "arn",
    [
        "arn:aws:sns:us-east-1:999999999999:dev-wwii-dedup-complete",  # foreign account
        "arn:aws:sns:eu-west-1:123456789012:dev-wwii-dedup-complete",  # foreign region
        "arn:aws:sns:us-east-1:123456789012:evil-topic",  # wrong name
        "arn:aws:sqs:us-east-1:123456789012:dev-wwii-dedup-complete",  # wrong service
        "not-an-arn",
        "",
    ],
)
def test_trusted_topic_rejects_foreign(monkeypatch, arn):
    monkeypatch.setenv("DEDUP_COMPLETE_TOPIC_ARN", OUR_ARN)
    assert g._is_trusted_dedup_topic(arn) is False


def test_trusted_topic_fallback_when_unset(monkeypatch):
    """Local/test (no ARN configured) falls back to legacy trust so non-AWS runs still work."""
    monkeypatch.delenv("DEDUP_COMPLETE_TOPIC_ARN", raising=False)
    assert g._is_trusted_dedup_topic("anything") is True


# --- handler flow: untrusted topic must NOT ungate Phase 3 ---


def _sns_event(topic_arn):
    return {
        "Records": [
            {
                "Sns": {
                    "TopicArn": topic_arn,
                    "Message": json.dumps({"dedup_complete": True}),
                }
            }
        ]
    }


@pytest.fixture
def _aws_gate(monkeypatch):
    """review complete + aws enabled, with storage + _trigger_phase3_for_all stubbed."""
    monkeypatch.setenv("S3_BUCKET", "b")
    monkeypatch.setenv("PHASE3_FUNCTION_NAME", "dev-wwii-phase3-enrich")
    monkeypatch.setenv("DEDUP_COMPLETE_TOPIC_ARN", OUR_ARN)
    storage = MagicMock()
    storage.read_json.return_value = {"complete": True}
    cfg = {"aws": {"enabled": True, "region": "us-east-1"}}
    monkeypatch.setattr(g, "_trigger_phase3_for_all", MagicMock(return_value=7))
    return storage, cfg


def test_handler_ungated_only_for_trusted_topic(_aws_gate, monkeypatch):
    storage, cfg = _aws_gate
    with (
        patch("src.utils.config.load_config", return_value=cfg),
        patch("src.utils.storage.S3Storage", return_value=storage),
        patch("boto3.client", return_value=MagicMock()),
    ):
        result = g.handler(_sns_event(OUR_ARN), None)
    assert result["action"] == "triggered_all"
    assert result["entities"] == 7
    g._trigger_phase3_for_all.assert_called_once()


def test_handler_drops_untrusted_topic(_aws_gate, monkeypatch):
    storage, cfg = _aws_gate
    foreign = "arn:aws:sns:us-east-1:999999999999:dev-wwii-dedup-complete"
    with (
        patch("src.utils.config.load_config", return_value=cfg),
        patch("src.utils.storage.S3Storage", return_value=storage),
        patch("boto3.client", return_value=MagicMock()),
    ):
        result = g.handler(_sns_event(foreign), None)
    # Phase 3 was NOT fanned out; the untrusted dedup_complete was dropped.
    g._trigger_phase3_for_all.assert_not_called()
    assert result["action"] != "triggered_all"
