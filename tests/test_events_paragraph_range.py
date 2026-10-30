"""Events validate-and-resubmit (user 2026-10-30): a Grok events response whose paragraph_numbers
fall mostly outside the input range (absolute_number 1..N) is a hallucination that would starve all
downstream entity extraction (people=0/places=0 fail_stop). Reject + retry before proceeding.
"""

import pytest
from jsonschema import ValidationError

from src.extraction.events import _validate_paragraph_range

_PARSED = {
    "paragraphs": [{"absolute_number": str(i), "text": "t"} for i in range(1, 264)]
}  # 1..263


def test_out_of_range_response_rejected():
    bad = {"Event": {"Sub-events": [{"paragraph_numbers": list(range(264, 365))}]}}
    with pytest.raises(ValidationError):
        _validate_paragraph_range(bad, _PARSED, 0)


def test_in_range_response_passes():
    good = {"Event": {"Sub-events": [{"paragraph_numbers": [1, 2, 3, 50, 263]}]}}
    _validate_paragraph_range(good, _PARSED, 0)  # must not raise


def test_few_stray_refs_tolerated():
    mostly = {
        "Event": {"Sub-events": [{"paragraph_numbers": [1, 2, 3, 4, 999]}]}
    }  # 1/5 out
    _validate_paragraph_range(mostly, _PARSED, 0)  # must not raise


def test_no_baseline_or_no_nums_is_noop():
    _validate_paragraph_range({"Event": {"Sub-events": []}}, _PARSED, 0)  # no nums
    _validate_paragraph_range(
        {"Event": {"Sub-events": [{"paragraph_numbers": [1]}]}}, {"paragraphs": []}, 0
    )  # no baseline


# ---- integration: the validate-and-RESUBMIT actually re-prompts (the review-caught bug) ----


def test_extract_events_resubmits_on_out_of_range_then_succeeds(tmp_path, monkeypatch):
    """Out-of-range paragraph_numbers on attempt 1 must drive a RE-PROMPT (not abort), then succeed
    on an in-range attempt 2. Regression for the escape bug where the ValidationError bypassed the
    retry loop (grok called only once)."""
    import json
    from unittest import mock
    from src.extraction import events as E

    parsed = {
        "book": "B",
        "author": "A",
        "series": "S",
        "chapter_title": "C",
        "chapter": "C",
        "paragraphs": [
            {"absolute_number": str(i), "text": f"para {i}"} for i in range(1, 11)
        ],
        "images": [],
        "maps": [],
    }
    pf = tmp_path / "chapter1full-parsed.json"
    pf.write_text(json.dumps(parsed))

    def _ev(nums):
        return {
            "Event": {
                "EventID": "0YQFZD6P4XYJ5BTC72QFMAQXYB",
                "Event_Name": "E",
                "Sub-events": [
                    {
                        "Sub-eventID": "01ULID0000000000000000000A",
                        "Sub-event_summary": "s",
                        "paragraph_numbers": nums,
                        "references": [],
                        "Footnote_References": [],
                    }
                ],
            }
        }

    calls = {"n": 0}

    def fake_extract_json(**kw):
        calls["n"] += 1
        return _ev(list(range(900, 1000))) if calls["n"] == 1 else _ev([1, 2, 3])

    grok = mock.Mock()
    grok.extract_json.side_effect = fake_extract_json
    grok._get_cache.return_value = {}
    grok._make_cache_key.return_value = "k"
    # let schema validation + save pass without heavy deps
    monkeypatch.setattr(E, "validate_event_json", lambda r: None)
    monkeypatch.setattr(E, "_save_event_output", lambda r, pf, od: od / "out.json")
    monkeypatch.setattr(E, "get_system_prompt", lambda n: None)

    out = E.extract_events(pf, grok, tmp_path, max_retries=3)
    assert (
        calls["n"] >= 2
    ), f"expected a RE-PROMPT after out-of-range, got {calls['n']} call(s)"
    assert out is not None


def test_extract_events_persistent_out_of_range_fails_loud(tmp_path, monkeypatch):
    import json
    from unittest import mock
    from src.extraction import events as E

    parsed = {
        "book": "B",
        "author": "A",
        "series": "S",
        "chapter_title": "C",
        "chapter": "C",
        "paragraphs": [
            {"absolute_number": str(i), "text": f"p{i}"} for i in range(1, 11)
        ],
        "images": [],
        "maps": [],
    }
    pf = tmp_path / "chapter1full-parsed.json"
    pf.write_text(json.dumps(parsed))

    def _ev():
        return {
            "Event": {
                "EventID": "0YQFZD6P4XYJ5BTC72QFMAQXYB",
                "Event_Name": "E",
                "Sub-events": [
                    {
                        "Sub-eventID": "01ULID0000000000000000000A",
                        "Sub-event_summary": "s",
                        "paragraph_numbers": list(range(900, 1000)),
                        "references": [],
                        "Footnote_References": [],
                    }
                ],
            }
        }

    grok = mock.Mock()
    grok.extract_json.side_effect = lambda **kw: _ev()
    grok._get_cache.return_value = {}
    grok._make_cache_key.return_value = "k"
    monkeypatch.setattr(E, "validate_event_json", lambda r: None)
    monkeypatch.setattr(E, "get_system_prompt", lambda n: None)
    import pytest as _pt

    with _pt.raises(
        Exception
    ):  # loud failure after max_retries (ValueError), not a silent degraded event
        E.extract_events(pf, grok, tmp_path, max_retries=2)
