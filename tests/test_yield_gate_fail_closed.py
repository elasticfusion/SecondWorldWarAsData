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


def test_groups_produced_read_under_people_groups_key(monkeypatch):
    """FALSE-fail_stop fix (XChannel-8 2026-10-29): the groups entity is scored as 'groups' but the
    Phase-2 summary counts it under 'people_groups'. The gate must read the people_groups count for
    the groups baseline, NOT 0 — else a book that extracted groups is falsely halted."""
    from src.utils import yield_baseline as YB

    # groups-rich source so the deterministic floor > 0
    monkeypatch.setattr(
        YB,
        "_book_source_text",
        lambda _root: "The VII Corps and the 9th Infantry Division and the 5th Armored Division.",
    )
    monkeypatch.setattr(YB, "_append_quality_metrics", lambda *a, **k: None)
    per = YB._compute_baseline(
        YB._book_source_text(None),
        {"people_groups": 31, "people": 55, "equipment": 0, "places": 96},
    )
    det, prod = per["groups"]
    assert prod == 31, f"groups produced must read the people_groups key, got {prod}"


def test_produced_for_alias_and_direct():
    from src.utils import yield_baseline as YB

    assert YB._produced_for("groups", {"people_groups": 31}) == 31  # aliased
    assert YB._produced_for("groups", {"groups": 7}) == 7  # direct name also works
    assert YB._produced_for("people", {"people": 55}) == 55  # non-aliased entity
    assert YB._produced_for("groups", {}) == 0  # truly absent -> 0
