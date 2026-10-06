"""Second mention of equipment: already-enriched records are NOT re-enriched; records
whose first enrichment FAILED (no enrichment_status) ARE retried on merge; _last_updated
is stamped on merge."""

import json
from pathlib import Path

import src.extraction.equipment as eq


class _CountingGrok:
    def __init__(self):
        self.enrich_calls = 0


def _write(tmp_path, record):
    f = tmp_path / "M4.json"
    f.write_text(json.dumps(record))
    return f


def _mention(ev="01EV2"):
    return {"MentionID": "01M2", "EventID": ev, "Sub_eventID": "01SUB2"}


def test_already_enriched_not_re_enriched(tmp_path, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        eq,
        "_enrich_and_add_media",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    )
    f = _write(
        tmp_path,
        {
            "common_name": "M4 Sherman",
            "technical_identifier": "M4",
            "enrichment_status": "enriched",
            "event_mentions": [{"EventID": "01EV1", "Sub_eventID": "01SUB1"}],
        },
    )
    eq._merge_into_existing(
        f,
        _mention(),
        {"common_name": "M4 Sherman"},
        "M4",
        grok_client=_CountingGrok(),
        verify_media_with_vision=False,
    )
    assert calls["n"] == 0  # never re-enriched
    rec = json.loads(f.read_text())
    assert len(rec["event_mentions"]) == 2  # mention still appended
    assert rec["_last_updated"]  # last-modified stamped


def test_failed_enrichment_retried_on_merge(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake(data, *a, **k):
        calls["n"] += 1
        data["specifications"] = {"weight_kg": 30300}

    monkeypatch.setattr(eq, "_enrich_and_add_media", fake)
    # Record saved WITHOUT enrichment_status -> first enrichment had failed.
    f = _write(
        tmp_path,
        {
            "common_name": "M4 Sherman",
            "technical_identifier": "M4",
            "event_mentions": [{"EventID": "01EV1", "Sub_eventID": "01SUB1"}],
        },
    )
    eq._merge_into_existing(
        f,
        _mention(),
        {"common_name": "M4 Sherman"},
        "M4",
        grok_client=_CountingGrok(),
        verify_media_with_vision=False,
    )
    assert calls["n"] == 1  # retried
    rec = json.loads(f.read_text())
    assert rec["enrichment_status"] == "enriched"
    assert rec["specifications"]["weight_kg"] == 30300


def test_no_grok_client_no_retry_but_still_stamps(tmp_path):
    f = _write(
        tmp_path,
        {
            "common_name": "M4 Sherman",
            "event_mentions": [{"EventID": "01EV1", "Sub_eventID": "01SUB1"}],
        },
    )
    eq._merge_into_existing(f, _mention(), {"common_name": "M4 Sherman"}, "M4")
    rec = json.loads(f.read_text())
    assert "enrichment_status" not in rec  # no client -> no enrich
    assert rec["_last_updated"]  # but still stamped last-modified
