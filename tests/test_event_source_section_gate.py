"""Regression: a dependent source_section must not be emitted when the event write is BLOCKED.

Root cause of the live source_section->events dangling bug: `_save_event_output` called
`write_json_with_lock` (which can block a schema-invalid event) and then UNCONDITIONALLY emitted
the source_section referencing that event's EventID — creating a dangling cross-reference at write
time. The fix gates the source_section emission on the event write actually persisting.
"""

from pathlib import Path

import src.extraction.events as events


def test_source_section_skipped_when_event_write_blocked(monkeypatch, tmp_path):
    emitted = {"called": False}

    # Event write BLOCKED (guard returns False -> not persisted).
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda *a, **k: False
    )
    monkeypatch.setattr(
        events,
        "_emit_source_section_safe",
        lambda *a, **k: emitted.__setitem__("called", True),
    )

    parsed = tmp_path / "chapter-parsed.json"
    parsed.write_text("{}")
    events._save_event_output({"Event": {"EventID": "x"}}, parsed, tmp_path)
    assert (
        emitted["called"] is False
    ), "source_section emitted despite blocked event write"


def test_source_section_emitted_when_event_write_succeeds(monkeypatch, tmp_path):
    emitted = {"called": False}
    monkeypatch.setattr(
        "src.utils.file_lock.write_json_with_lock", lambda *a, **k: True
    )
    monkeypatch.setattr(
        events,
        "_emit_source_section_safe",
        lambda *a, **k: emitted.__setitem__("called", True),
    )
    parsed = tmp_path / "chapter-parsed.json"
    parsed.write_text("{}")
    events._save_event_output({"Event": {"EventID": "x"}}, parsed, tmp_path)
    assert emitted["called"] is True
