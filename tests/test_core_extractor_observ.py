"""PR-C: un-gated core entity extractors must route every failure/drop through the observe() sink.

Governing rule (PATTERN-8): every failure/drop/degraded path is a LOUD canonical OBSERV at the
point of loss — never generic-only, never silent, never count=0-as-ok. These regression tests pin
the four audit findings:

  #6  casualties._call_and_parse_casualties / _parse_casualty_response -> {}  (retry-exhaustion and
      unparseable-JSON) must emit `OBSERV casualties.extract_error status=error produced=0`.
  #7  casualties.extract_casualties has no yield rollup -> add a canonical
      `OBSERV casualties.extract status=ok produced=<n>` so a {}-batch -> [] is visible.
  #8  people_groups: JSONDecodeError path returned groups_dir BEFORE the yield observer ran ->
      emit `OBSERV groups.parse_error status=error` and run the yield observer before returning.
  #16 people_groups: GrokTruncationError -> return None skipped the yield observer ->
      emit `OBSERV groups.truncation_error status=error` and run the yield observer before return.

All assertions read the ROOT logger via caplog (the `observe` sink logs under its own logger) and
delete NOTIFICATION_TOPIC_ARN so no real Slack/SNS escalation is attempted (fail-safe, log-only).
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.extraction import casualties as C
from src.extraction import people_groups as PG
from src.grok_client import GrokTruncationError


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    # observe(status=error) escalates to Slack only when NOTIFICATION_TOPIC_ARN is set; unset it so
    # the sink stays strictly log-only in tests (no SNS publish, no network).
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)


# --------------------------------------------------------------------------------------------- #
# #6  casualties: unparseable-JSON and retry-exhaustion {} returns are loud + canonical.
# --------------------------------------------------------------------------------------------- #


def test_parse_casualty_no_json_object_emits_observ(caplog):
    with caplog.at_level(logging.ERROR):
        out = C._parse_casualty_response("no braces here at all")
    assert out == {}
    assert "OBSERV casualties.extract_error" in caplog.text
    assert "status=error" in caplog.text
    assert "produced=0" in caplog.text
    assert "reason=no_json_object" in caplog.text


def test_parse_casualty_non_dict_emits_observ(caplog):
    # The brace-slice always starts at '{', so json.loads yields a dict or raises; the non-dict
    # branch is a defensive guard. Patch json.loads to return a list to exercise it directly.
    import src.extraction.casualties as _C

    original = _C.json.loads
    try:
        _C.json.loads = lambda *a, **k: ["a", "b"]  # type: ignore[assignment]
        with caplog.at_level(logging.ERROR):
            out = C._parse_casualty_response('{"ignored": 1}')
    finally:
        _C.json.loads = original
    assert out == {}
    assert "OBSERV casualties.extract_error" in caplog.text
    assert "reason=not_a_dict" in caplog.text


def test_parse_casualty_valid_does_not_emit_error(caplog):
    with caplog.at_level(logging.ERROR):
        out = C._parse_casualty_response('{"S1": []}')
    assert out == {"S1": []}
    assert "casualties.extract_error" not in caplog.text


def test_call_and_parse_retry_exhaustion_emits_observ(caplog):
    grok = MagicMock()
    grok.chat_completion.side_effect = ValueError("boom")  # every attempt fails
    with caplog.at_level(logging.ERROR):
        out = C._call_and_parse_casualties(grok, "prompt", cache_key="k")
    assert out == {}
    assert "OBSERV casualties.extract_error" in caplog.text
    assert "status=error" in caplog.text
    assert "produced=0" in caplog.text
    assert "reason=retry_exhausted" in caplog.text


# --------------------------------------------------------------------------------------------- #
# #7  casualties.extract_casualties emits a canonical produced-count rollup (even for {} -> []).
# --------------------------------------------------------------------------------------------- #


def _write_event(tmp_path: Path) -> Path:
    event = {
        "Book": "Breakout and Pursuit",
        "Chapter": "C1",
        "Event": {
            "EventID": "01EVENT",
            "Sub-events": [
                {
                    "Sub-eventID": "01SUB",
                    "description": "Heavy casualties; many men killed.",
                }
            ],
        },
    }
    f = tmp_path / "event.json"
    f.write_text(json.dumps(event), encoding="utf-8")
    return f


def test_extract_casualties_empty_batch_emits_rollup(tmp_path, monkeypatch, caplog):
    event_file = _write_event(tmp_path)
    output_root = tmp_path / "out"
    output_root.mkdir()

    # Force the batch extractor to yield nothing -> casualties == [] (the {}-batch -> [] case).
    monkeypatch.setattr(C, "_batch_extract_casualties", lambda *a, **k: {})
    grok = MagicMock()

    with caplog.at_level(logging.INFO):
        result = C.extract_casualties(event_file, output_root, grok)

    assert result == []
    assert "OBSERV casualties.extract" in caplog.text
    assert "status=ok" in caplog.text
    assert "produced=0" in caplog.text
    assert "event_id=01EVENT" in caplog.text


def test_observe_casualties_rollup_handles_missing_event_id(caplog):
    with caplog.at_level(logging.INFO):
        C._observe_casualties_rollup(None, 0, 0)
    assert "OBSERV casualties.extract" in caplog.text
    assert "event_id=?" in caplog.text


# --------------------------------------------------------------------------------------------- #
# #8 / #16  people_groups failure paths are loud + canonical AND run the yield observer.
# --------------------------------------------------------------------------------------------- #


def _write_group_event(tmp_path: Path) -> Path:
    event = {
        "book": "Breakout and Pursuit",
        "author": "Blumenson",
        "series": "USAWW2",
        "Event": {
            "EventID": "01GRPEVENT",
            "Sub-events": [
                {
                    "Sub-eventID": "01GSUB",
                    "Sub-event_fulltext": {
                        "Paragraph_1": "The 358th Infantry advanced."
                    },
                }
            ],
        },
    }
    f = tmp_path / "group_event.json"
    f.write_text(json.dumps(event), encoding="utf-8")
    return f


def test_groups_truncation_emits_observ_and_yield(tmp_path, caplog):
    event_file = _write_group_event(tmp_path)
    grok = MagicMock()
    grok.chat_completion.side_effect = GrokTruncationError("too large")

    with caplog.at_level(logging.INFO):
        result = PG.extract_people_groups(event_file, grok, tmp_path)

    assert result is None
    # #16 canonical point-of-loss anomaly ...
    assert "OBSERV groups.truncation_error" in caplog.text
    assert "status=error" in caplog.text
    assert "produced=0" in caplog.text
    # ... AND the yield observer ran before return (produced=0 baseline visible).
    assert "OBSERV yield.groups" in caplog.text


def test_groups_parse_error_emits_observ_and_yield(tmp_path, caplog):
    event_file = _write_group_event(tmp_path)
    grok = MagicMock()
    grok.chat_completion.return_value = "this is not json {{{"

    with caplog.at_level(logging.INFO):
        result = PG.extract_people_groups(event_file, grok, tmp_path)

    # #8: behavior preserved (returns the groups_dir) BUT the drop is now loud + canonical and the
    # yield observer runs AHEAD of the early return.
    assert result == tmp_path / "people_groups"
    assert "OBSERV groups.parse_error" in caplog.text
    assert "status=error" in caplog.text
    assert "produced=0" in caplog.text
    assert "OBSERV yield.groups" in caplog.text


def test_groups_empty_response_emits_observ_and_yield(tmp_path, caplog):
    event_file = _write_group_event(tmp_path)
    grok = MagicMock()
    grok.chat_completion.return_value = ""  # empty response -> point-of-loss drop

    with caplog.at_level(logging.INFO):
        result = PG.extract_people_groups(event_file, grok, tmp_path)

    assert result is None
    assert "OBSERV groups.empty_response" in caplog.text
    assert "status=error" in caplog.text
    assert "OBSERV yield.groups" in caplog.text
