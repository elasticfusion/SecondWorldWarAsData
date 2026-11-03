"""DRY P1.4 guard: the 27 inline logger.warning/error("OBSERV ...") strings now route through the
observe() sink (logger name "observe"), so they get the sink's newline-sanitization + Slack
error-escalation (status=error). Previously they were bare module-logger lines that never escalated
and weren't sanitized. These tests pin the sink routing + the two properties that bare logs lacked.
"""

import logging


def _sink_lines(caplog):
    """OBSERV lines emitted through the observe() sink (logger name 'observe')."""
    return [r.getMessage() for r in caplog.records if r.name == "observe"]


def test_converted_site_routes_through_observe_sink(monkeypatch, tmp_path):
    # A real converted call site: file_lock._atomic_write_json save-failure now calls observe_warn
    # (was a bare logger.warning("OBSERV locked_json.save_failed ...")). Spy observe_warn and force
    # the atomic replace to fail; assert the sink helper is invoked with the right scope/event, then
    # the error re-raises (unchanged behavior).
    import os
    import pytest
    import src.utils.observe as obs
    import src.utils.file_lock as fl

    calls = []
    monkeypatch.setattr(
        obs, "observe_warn", lambda scope, event, **kw: calls.append((scope, event, kw))
    )
    monkeypatch.setattr(
        os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    with pytest.raises(BaseException):
        fl._atomic_write_json(tmp_path / "x.json", {"k": 1})
    assert ("locked_json", "save_failed") in [(s, e) for (s, e, _) in calls], calls


def test_observe_error_emits_status_error(caplog):
    # The sink escalates status=error to Slack; a bare logger.error never did. Proven: converted
    # error sites use observe_error -> status=error on the sink logger.
    from src.utils.observe import observe_error

    with caplog.at_level(logging.ERROR):
        observe_error("phase2", "summary", reason="SmokeTest")
    lines = _sink_lines(caplog)
    assert any(
        l.startswith("OBSERV phase2.summary status=error") and "reason=SmokeTest" in l
        for l in lines
    ), lines


def test_sink_sanitizes_newline_injection(caplog):
    # A model/caller-derived field with a newline must NOT forge a second OBSERV line (log
    # integrity) — the exact protection a bare logger.warning string lacked.
    from src.utils.observe import observe_warn

    with caplog.at_level(logging.WARNING):
        observe_warn(
            "image_place", "error", reason="line1\nOBSERV forged.injection status=error"
        )
    lines = _sink_lines(caplog)
    assert len(lines) == 1  # one record, not two
    assert "\n" not in lines[0]  # newline neutralized
    assert (
        "forged.injection" in lines[0]
    )  # present as sanitized text within the one line
