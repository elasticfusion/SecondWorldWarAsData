"""Tests for the validation statistics catcher (data-quality telemetry)."""

from unittest.mock import MagicMock, patch

import src.utils.validation_stats as vs


def setup_function(_):
    vs.reset_stats()


def test_counts_and_block_rate():
    for _ in range(90):
        vs.record_validation("people", "allow")
    for _ in range(10):
        vs.record_validation("people", "block", "required", "ok")
    snap = vs.get_stats()
    assert snap["totals"] == {"allow": 90, "block": 10}
    assert snap["block_rate"] == 0.1


def test_systematic_cluster_detected_above_thresholds():
    for _ in range(88):
        vs.record_validation("people", "allow")
    for _ in range(12):  # 12/100 = 12% >= 5%, count 12 >= 10 -> systematic
        vs.record_validation("people", "block", "required", "ok")
    clusters = vs.systematic_clusters()
    assert len(clusters) == 1
    assert clusters[0]["entity"] == "people" and clusters[0]["keyword"] == "required"
    assert clusters[0]["rate"] == 0.12


def test_isolated_failures_not_systematic():
    # Below BOTH count and rate thresholds -> not a cluster (transient; auto-reprocess handles).
    for _ in range(200):
        vs.record_validation("places", "allow")
    for _ in range(3):
        vs.record_validation("places", "block", "pattern", "ok")
    assert vs.systematic_clusters() == []


def test_count_high_but_rate_low_not_systematic():
    # 12 blocks but out of 100000 writes -> 0.012% -> not systematic (noise at scale).
    for _ in range(100000):
        vs.record_validation("dates", "allow")
    for _ in range(12):
        vs.record_validation("dates", "block", "required", "ok")
    assert vs.systematic_clusters() == []


def test_alert_fires_on_systematic(monkeypatch):
    monkeypatch.setenv(
        "NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:dev-wwii-phase2-complete"
    )
    for _ in range(88):
        vs.record_validation("people", "allow")
    for _ in range(12):
        vs.record_validation("people", "block", "required", "ok")
    sns = MagicMock()
    with patch("boto3.client", return_value=sns):
        vs.write_validation_stats("/tmp/vstest_alert")
    sns.publish.assert_called_once()
    body = sns.publish.call_args.kwargs["Message"]
    assert "systematic validation failures" in body.lower()
    assert "people" in body and "required" in body
    # PII-safe: no record values, only aggregate counts
    assert "12/100" in body


def test_no_alert_when_no_systematic(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:topic")
    for _ in range(50):
        vs.record_validation("places", "allow")
    sns = MagicMock()
    with patch("boto3.client", return_value=sns):
        vs.write_validation_stats("/tmp/vstest_noalert")
    sns.publish.assert_not_called()


def test_record_is_failsafe():
    # Must never raise even on bad input.
    vs.record_validation(None, None)  # type: ignore[arg-type]
    assert isinstance(vs.get_stats(), dict)
