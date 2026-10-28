"""PR-D integrity/gate observability hardening (TOP-10 #7 + patterns 4,6).

Covers three fixes, each FAIL-SAFE:
  1. referential_integrity.run_for_phase stamps status='unknown' on TIMEOUT/skip/error (so a
     timed-out scan can NEVER be read as "clean, 0 dangling"); status='ok' only on a COMPLETED
     clean scan.
  2. provenance_integrity.run_for_phase emits an SNS/alert-parity signal (loud OBSERV
     provenance.alert WARNING + best-effort SNS) when a record is missing provenance or an entity
     is kind=='none' — previously anomalies were only logged, never alerted.
  3. validation_stats small-batch escalation: a 100%-empty SMALL batch (e.g. 7 places all empty)
     still WARNs even though it is below the large-batch count>=10 floor.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import src.utils.provenance_integrity as PI
import src.utils.referential_integrity as RI
import src.utils.validation_stats as vs

# --------------------------------------------------------------------------------------------
# 1. referential_integrity: timeout/skip/error => status='unknown'; completed clean => 'ok'
# --------------------------------------------------------------------------------------------


def test_ref_skip_env_returns_status_unknown(monkeypatch, caplog):
    monkeypatch.setenv("SKIP_PHASE_REFERENTIAL_ENFORCE", "1")
    with caplog.at_level(logging.INFO, logger=RI.__name__):
        rep = RI.run_for_phase("/tmp/does-not-matter", phase="Phase 2")
    assert rep["status"] == "unknown"
    assert rep["total_dangling"] == 0
    assert rep["skipped"] == "env"


def test_ref_timeout_returns_status_unknown(monkeypatch, caplog):
    """A scan that blows the timeout must return status='unknown', not a clean 0-dangling report."""
    monkeypatch.delenv("SKIP_PHASE_REFERENTIAL_ENFORCE", raising=False)
    monkeypatch.setenv("PHASE_REFERENTIAL_ENFORCE_TIMEOUT", "1")

    import time

    def _slow(output_root, phase):  # noqa: ANN001 - test stub
        time.sleep(5)
        return {"edges": [], "total_dangling": 0, "status": "ok"}

    monkeypatch.setattr(RI, "_enforce_for_phase", _slow)
    with caplog.at_level(logging.WARNING, logger=RI.__name__):
        rep = RI.run_for_phase("/tmp/x", phase="Phase 1")
    assert rep["status"] == "unknown"
    assert rep["total_dangling"] == 0
    assert rep["skipped"] == "timeout"
    assert "status=unknown" in caplog.text


def test_ref_enforce_error_returns_status_unknown(monkeypatch):
    monkeypatch.delenv("SKIP_PHASE_REFERENTIAL_ENFORCE", raising=False)
    monkeypatch.setenv("PHASE_REFERENTIAL_ENFORCE_TIMEOUT", "30")
    # Force the storage resolution/enforce to fail -> _enforce_for_phase returns an unknown report.
    monkeypatch.setattr(
        RI,
        "_enforce_for_phase",
        lambda output_root, phase: RI._unknown_report(error="storage down"),
    )
    rep = RI.run_for_phase("/tmp/x", phase="Phase 3")
    assert rep["status"] == "unknown"
    assert rep["error"] == "storage down"
    assert rep["total_dangling"] == 0


def test_ref_completed_clean_scan_returns_status_ok(tmp_path, monkeypatch):
    """A COMPLETED scan over an empty (clean) corpus => status='ok' + total_dangling 0 that now
    genuinely means clean (distinguishable from the unknown timeout case)."""
    monkeypatch.delenv("SKIP_PHASE_REFERENTIAL_ENFORCE", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    monkeypatch.setenv("PHASE_REFERENTIAL_ENFORCE_TIMEOUT", "30")
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    (tmp_path / "output").mkdir()
    rep = RI.run_for_phase(tmp_path / "output", phase="Phase 2")
    assert rep["status"] == "ok"
    assert rep["total_dangling"] == 0
    assert "skipped" not in rep


def test_ref_unknown_report_helper():
    assert RI._unknown_report(skipped="env") == {
        "edges": [],
        "total_dangling": 0,
        "status": "unknown",
        "skipped": "env",
    }
    assert RI._unknown_report(error="x")["status"] == "unknown"


# --------------------------------------------------------------------------------------------
# 2. provenance_integrity: alert-parity signal on missing-provenance / kind=='none'
# --------------------------------------------------------------------------------------------


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_prov_alert_fires_on_missing_provenance(tmp_path, monkeypatch, caplog):
    # a place with NO provenance edge at all -> missing_provenance anomaly.
    _write(tmp_path / "places" / "nowhere.json", {"PlaceID": "01P", "name": "nowhere"})
    monkeypatch.setenv(
        "NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:dev-wwii-phase2-complete"
    )
    sns = MagicMock()
    with patch("boto3.client", return_value=sns):
        with caplog.at_level(logging.WARNING, logger=PI.__name__):
            rep = PI.run_for_phase(tmp_path, phase="Phase 2")
    # loud, greppable in-log alert fired
    assert "OBSERV provenance.alert phase=Phase 2" in caplog.text
    # SNS parity signal published (aggregate, PII-safe)
    sns.publish.assert_called_once()
    body = sns.publish.call_args.kwargs["Message"]
    assert "provenance" in body.lower()
    assert "Missing-provenance records: 1" in body
    assert rep["total_missing_provenance"] == 1


def test_prov_no_alert_on_clean_corpus(tmp_path, monkeypatch, caplog):
    # every record carries a populated provenance edge -> no anomaly -> no alert.
    _write(
        tmp_path / "dates" / "d.json",
        {"DateID": "01DATE", "event_mentions": [{"EventID": "01EID"}]},
    )
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:topic")
    sns = MagicMock()
    with patch("boto3.client", return_value=sns):
        with caplog.at_level(logging.WARNING, logger=PI.__name__):
            PI.run_for_phase(tmp_path, phase="Phase 2")
    assert "OBSERV provenance.alert" not in caplog.text
    sns.publish.assert_not_called()


def test_prov_alert_warns_even_without_topic(tmp_path, monkeypatch, caplog):
    """FAIL-SAFE parity: with no SNS topic configured the loud OBSERV WARNING still fires (the
    in-log alert monitors key on it); SNS wiring is the documented follow-up."""
    _write(tmp_path / "places" / "nowhere.json", {"PlaceID": "01P", "name": "nowhere"})
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    with caplog.at_level(logging.WARNING, logger=PI.__name__):
        PI.run_for_phase(tmp_path, phase="Phase 2")
    assert "OBSERV provenance.alert" in caplog.text


def test_prov_alert_is_failsafe(tmp_path, monkeypatch):
    """A broken SNS client must NOT crash the phase (alerting is best-effort)."""
    _write(tmp_path / "places" / "nowhere.json", {"PlaceID": "01P", "name": "nowhere"})
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:topic")
    with patch("boto3.client", side_effect=RuntimeError("no creds")):
        rep = PI.run_for_phase(tmp_path, phase="Phase 2")
    assert rep["total_missing_provenance"] == 1  # returned normally despite SNS failure


def test_prov_anomaly_total_helper():
    assert (
        PI._provenance_anomaly_total(
            {
                "total_missing_provenance": 2,
                "total_placeholder_records": 1,
                "structural_gaps": ["casualties"],
            }
        )
        == 4
    )
    assert (
        PI._provenance_anomaly_total(
            {
                "total_missing_provenance": 0,
                "total_placeholder_records": 0,
                "structural_gaps": [],
            }
        )
        == 0
    )


# --------------------------------------------------------------------------------------------
# 3. validation_stats: small-batch escalation (100%-empty small batch still WARNs)
# --------------------------------------------------------------------------------------------


def setup_function(_):
    vs.reset_stats()


def test_small_batch_all_empty_escalates():
    """7 places, all empty -> below the large-batch count>=10 floor, but 100% rate -> WARN."""
    for _ in range(7):
        vs.record_validation("places", "allow_empty", "below_floor", "ok")
    clusters = vs.systematic_clusters()
    assert len(clusters) == 1
    c = clusters[0]
    assert c["entity"] == "places"
    assert c["outcome"] == "allow_empty"
    assert c["count"] == 7
    assert c["rate"] == 1.0


def test_small_batch_partial_does_not_escalate():
    """A small batch that is NOT near-totally degraded stays non-systematic (3/10 = 30% < 90%)."""
    for _ in range(7):
        vs.record_validation("places", "allow")
    for _ in range(3):
        vs.record_validation("places", "allow_empty", "below_floor", "ok")
    assert vs.systematic_clusters() == []


def test_small_batch_below_min_count_does_not_escalate():
    """Below the small-batch min count (2 < 3) -> no escalation even at 100%."""
    for _ in range(2):
        vs.record_validation("places", "block", "required", "ok")
    assert vs.systematic_clusters() == []


def test_small_batch_alert_body(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:topic")
    for _ in range(7):
        vs.record_validation("places", "allow_empty", "below_floor", "ok")
    sns = MagicMock()
    with patch("boto3.client", return_value=sns):
        vs.write_validation_stats("/tmp/vstest_smallbatch")
    sns.publish.assert_called_once()
    body = sns.publish.call_args.kwargs["Message"]
    assert "places" in body and "allow_empty" in body
    assert "7/7" in body


def test_qualifies_as_cluster_rules():
    # large-batch rule: enough volume + non-trivial share
    assert vs._qualifies_as_cluster(10, 0.05) is True
    assert vs._qualifies_as_cluster(10, 0.04) is False
    # small-batch rule: small but near-totally degraded
    assert vs._qualifies_as_cluster(3, 0.9) is True
    assert (
        vs._qualifies_as_cluster(9, 0.99) is True
    )  # small-batch catches it (<10 count)
    assert vs._qualifies_as_cluster(3, 0.89) is False
    assert vs._qualifies_as_cluster(2, 1.0) is False
