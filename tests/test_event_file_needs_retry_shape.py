"""Guard: a healthy event file must NOT be judged "empty" and re-extracted.

Event files are written as ``{"Event": {"Sub-events": [...]}}``. The retry check read only
a top-level ``Sub-events`` key, so every healthy file looked empty -> cache-clearing
re-extraction -> NEW Sub-eventIDs -> the first pass's entity mentions were left dangling
(observed e2e: 31 dangling ``event_mentions[].Sub_eventID`` refs).
"""

import asyncio
import json
import logging

import phase2_extract
from src.extraction import batch_parallel


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_canonical_nested_event_file_is_not_retried(tmp_path):
    ef = _write(
        tmp_path / "ch-event.json",
        {"Event": {"EventID": "E", "Sub-events": [{"Sub-eventID": "S1"}]}},
    )
    assert phase2_extract._event_file_needs_retry(ef) is False


def test_nested_but_empty_sub_events_is_retried(tmp_path):
    ef = _write(
        tmp_path / "ch-event.json", {"Event": {"EventID": "E", "Sub-events": []}}
    )
    assert phase2_extract._event_file_needs_retry(ef) is True


def test_legacy_top_level_sub_events_still_accepted(tmp_path):
    ef = _write(tmp_path / "ch-event.json", {"Sub-events": [{"Sub-eventID": "S1"}]})
    assert phase2_extract._event_file_needs_retry(ef) is False


def test_missing_or_corrupt_is_retried(tmp_path):
    assert phase2_extract._event_file_needs_retry(tmp_path / "nope-event.json") is True
    bad = tmp_path / "bad-event.json"
    bad.write_text("{not json", encoding="utf-8")
    assert phase2_extract._event_file_needs_retry(bad) is True
    lst = _write(tmp_path / "list-event.json", [1, 2])
    assert phase2_extract._event_file_needs_retry(lst) is True


def test_retry_pass_skips_healthy_nested_files(tmp_path):
    """End-to-end over _retry_missing_events: no re-extraction for a healthy chapter."""
    pf = _write(tmp_path / "ch-parsed.json", {"paragraphs": ["p"]})
    _write(
        tmp_path / "ch-event.json",
        {"Event": {"EventID": "E", "Sub-events": [{"Sub-eventID": "S1"}]}},
    )

    class _Grok:
        batch_mode = False

        def clear_cache_entry(self, *a, **k):  # pragma: no cover - must not be hit
            raise AssertionError("healthy chapter was re-extracted")

    retried, failed = phase2_extract._retry_missing_events(
        [pf], _Grok(), logging.getLogger("t")
    )
    assert (retried, failed) == (0, 0)


def test_timeout_failure_reason_is_not_empty(caplog):
    caplog.set_level(logging.ERROR)
    results = {"processed": 0, "failed": 0}
    batch_parallel._process_batch_results(
        [("Book", "ch-parsed.json", None)], [asyncio.TimeoutError()], results
    )
    assert results["failed"] == 1
    line = next(r.getMessage() for r in caplog.records if "✗" in r.getMessage())
    assert "TimeoutError" in line
    assert not line.rstrip().endswith(":")


def test_failure_reason_keeps_message():
    assert batch_parallel._failure_reason(ValueError("boom")) == "ValueError: boom"
    assert batch_parallel._failure_reason(KeyError()) == "KeyError"
