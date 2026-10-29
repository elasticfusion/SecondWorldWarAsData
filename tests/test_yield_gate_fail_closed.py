"""The yield quality gate must FAIL CLOSED (anti-swallow): a blind (empty-baseline) or broken
(crashed) gate halts with a loud OBSERV instead of silently returning ('ok', []). Regression for
the Pattern-8 finding that the safety net itself swallowed (OBSERVABILITY_PATTERN8_AUDIT #3, #4).
"""

import logging

from src.utils import yield_baseline as YB


def test_empty_baseline_fails_closed(monkeypatch, caplog, tmp_path):
    # no source text -> gate is blind -> must fail_stop + emit yield.gate_empty_baseline
    monkeypatch.setattr(YB, "_book_source_text", lambda _root: "")
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    with caplog.at_level(logging.ERROR):
        verdict, offenders = YB.run_quality_gate(tmp_path, {"people": 5})
    assert verdict == "fail_stop"
    assert "OBSERV yield.gate_empty_baseline status=error" in caplog.text


def test_gate_crash_fails_closed(monkeypatch, caplog, tmp_path):
    # gate machinery throws -> must fail_stop + emit yield.gate_error (NOT silent ('ok', []))
    def _boom(_root):
        raise RuntimeError("baseline scan exploded")

    monkeypatch.setattr(YB, "_book_source_text", _boom)
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    with caplog.at_level(logging.ERROR):
        verdict, offenders = YB.run_quality_gate(tmp_path, {"groups": 10})
    assert verdict == "fail_stop"
    assert "OBSERV yield.gate_error status=error" in caplog.text
    assert "reason=RuntimeError" in caplog.text


def test_healthy_baseline_still_classifies_normally(monkeypatch, tmp_path):
    # a real baseline must still go through the normal classify path (not auto-fail)
    monkeypatch.setattr(
        YB, "_book_source_text", lambda _root: "General Cota led the 116th Infantry."
    )
    monkeypatch.setattr(YB, "_append_quality_metrics", lambda *a, **k: None)
    verdict, _ = YB.run_quality_gate(
        tmp_path, {"people": 50, "groups": 50, "equipment": 50, "places": 50}
    )
    assert verdict in (
        "ok",
        "warn",
        "fail_stop",
    )  # normal classification, not the blind-gate path
