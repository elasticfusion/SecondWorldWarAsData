"""Tests for the dedup gate decision (safe + observable auto-proceed).

Covers the three hardening changes:
1. Missing/unreadable dedup report must NOT silently auto-proceed.
2. The gate decision is reported (auto_proceed vs block) for notification.
3. A suspicious zero (many entities, zero groups) blocks for human review.
"""

import json
import os
from pathlib import Path

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint


def _write_reports(root: Path, groups_by_type: dict, missing=()):
    for subdir in ["people", "people_groups", "places", "equipment"]:
        d = root / "output" / subdir
        d.mkdir(parents=True, exist_ok=True)
        if subdir in missing:
            continue
        (d / "duplicate_report.json").write_text(
            json.dumps({"duplicate_groups": groups_by_type.get(subdir, 0)})
        )


def _seed_entities(root: Path, n: int):
    d = root / "output" / "people"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"p{i}.json").write_text("{}")


def test_missing_report_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    _write_reports(tmp_path, {}, missing=("equipment",))
    action, reason = ecs_entrypoint._dedup_gate_decision(dedup_ok=True)
    assert action == "block"


def test_pending_groups_block(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    _write_reports(tmp_path, {"people": 3})
    action, _ = ecs_entrypoint._dedup_gate_decision(dedup_ok=True)
    assert action == "block"


def test_detection_failed_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    _write_reports(tmp_path, {})
    action, _ = ecs_entrypoint._dedup_gate_decision(dedup_ok=False)
    assert action == "block"


def test_suspicious_zero_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    monkeypatch.setenv("DEDUP_ZERO_SUSPICION_THRESHOLD", "500")
    _write_reports(tmp_path, {})
    _seed_entities(tmp_path, 600)  # >= threshold with zero groups → suspicious
    action, reason = ecs_entrypoint._dedup_gate_decision(dedup_ok=True)
    assert action == "block"
    assert "suspicious" in reason


def test_small_clean_run_auto_proceeds(tmp_path, monkeypatch):
    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    monkeypatch.setenv("DEDUP_ZERO_SUSPICION_THRESHOLD", "500")
    _write_reports(tmp_path, {})
    _seed_entities(tmp_path, 10)  # small, zero groups → safe to proceed
    action, _ = ecs_entrypoint._dedup_gate_decision(dedup_ok=True)
    assert action == "auto_proceed"


def test_notify_is_non_blocking(tmp_path, monkeypatch):
    # No NOTIFICATION_TOPIC_ARN set → must return without error.
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    ecs_entrypoint._notify_dedup_gate("auto_proceed", "test")


def test_block_alert_includes_dedup_review_url(monkeypatch):
    """The dedup-gate BLOCK alert must carry the Dedup Review UI deep link so the operator
    can act on it (previously the email said 'blocked' with no URL)."""
    import ecs_entrypoint
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    monkeypatch.setenv(
        "DEDUP_REVIEW_URL", "https://abc.execute-api.us-east-1.amazonaws.com/app/dedup"
    )
    sns = MagicMock()
    with patch("ecs_entrypoint.boto3.client", return_value=sns):
        ecs_entrypoint._notify_dedup_gate("block", "duplicate groups pending review")
    msg = sns.publish.call_args.kwargs["Message"]
    assert (
        "Dedup Review UI: https://abc.execute-api.us-east-1.amazonaws.com/app/dedup"
        in msg
    )


def test_auto_proceed_alert_omits_url(monkeypatch):
    """Auto-proceed needs no action link (nothing to review)."""
    import ecs_entrypoint
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    monkeypatch.setenv(
        "DEDUP_REVIEW_URL", "https://abc.execute-api.us-east-1.amazonaws.com/app/dedup"
    )
    sns = MagicMock()
    with patch("ecs_entrypoint.boto3.client", return_value=sns):
        ecs_entrypoint._notify_dedup_gate("auto_proceed", "clean")
    msg = sns.publish.call_args.kwargs["Message"]
    assert "Dedup Review UI" not in msg
