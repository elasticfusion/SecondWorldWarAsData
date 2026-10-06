"""#3: event_mentions dedup keyed on (Sub_eventID, time_start, original_text) — a sub-event
citing the same date at two different times/wordings keeps BOTH; exact re-run skips."""

import json
from src.extraction.dates import _add_event_mention

UL = "01ABCDEFGH0123456789ABCDEF"
SE = "01SUBEVENT0000000000000000"
EV = "01EVENT0000000000000000000"


def _write_date(tmp_path):
    f = tmp_path / "19450105_01ABCDEF.json"
    f.write_text(
        json.dumps({"DateID": UL, "date_start": "1945-01-05", "event_mentions": []})
    )
    return f


def _add(f, time_start, text):
    _add_event_mention(
        f,
        {"time_start": time_start, "original_text": text},
        "Battle",
        EV,
        "Opening barrage",
        SE,
        "Book",
        "Auth",
        "Series",
    )


def test_same_subevent_two_times_both_kept(tmp_path):
    f = _write_date(tmp_path)
    _add(f, "05:00", "the barrage opened at 0500")
    _add(f, "18:00", "by 1800 the position had fallen")
    ms = json.loads(f.read_text())["event_mentions"]
    assert len(ms) == 2  # different times -> both kept (was 1 before the fix)
    assert {m["time_start"] for m in ms} == {"05:00", "18:00"}


def test_exact_rerun_skipped(tmp_path):
    f = _write_date(tmp_path)
    _add(f, "05:00", "the barrage opened at 0500")
    _add(f, "05:00", "the barrage opened at 0500")  # identical -> skip
    assert len(json.loads(f.read_text())["event_mentions"]) == 1


def test_same_time_different_wording_kept(tmp_path):
    f = _write_date(tmp_path)
    _add(f, None, "fighting at the crossroads")
    _add(f, None, "the regiment held Malmedy")  # same date/subevent, different sentence
    assert len(json.loads(f.read_text())["event_mentions"]) == 2
