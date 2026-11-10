"""An events answer that leaves most of the chapter in no sub-event is rejected like an invalid one
(Cross-Channel Attack ch. VIII, 2026-11-10: a complete, valid batch answer put paragraphs 1-43 of
206 in 7 sub-events; the other 163 paragraphs' people, units and places were never extracted, and
the coverage alert only logged it). With the Grok cache on, the rejected answer is evicted - or every
re-run would replay it - and kept for diagnosis."""

import json
from unittest import mock

import pytest

from src.extraction import events as E

N = 20  # paragraphs in the test chapter


def _parsed_file(tmp_path):
    parsed = {
        "book": "B",
        "author": "A",
        "series": "S",
        "chapter_title": "C",
        "chapter": "C",
        "paragraphs": [
            {"absolute_number": i, "text": f"narrative paragraph {i:02d} " * 10}
            for i in range(1, N + 1)
        ],
        "images": [],
        "maps": [],
    }
    book = tmp_path / "content" / "B"
    book.mkdir(parents=True)
    pf = book / "chapter8full-parsed.json"
    pf.write_text(json.dumps(parsed))
    return pf


def _answer(*paragraph_sets):
    return {
        "Event": {
            "EventID": "0YQFZD6P4XYJ5BTC72QFMAQXYB",
            "Event_Name": "The Sixth of June",
            "Sub-events": [
                {
                    "Sub-eventID": f"01ULID000000000000000000{i:02d}",
                    "Sub-event_summary": f"part {i}",
                    "paragraph_numbers": list(ps),
                    "references": [],
                    "Footnote_References": [],
                }
                for i, ps in enumerate(paragraph_sets)
            ],
        }
    }


LOW = _answer(range(1, 4))  # 3 of 20 paragraphs: 15%
FULL = _answer(range(1, 11), range(11, N + 1))


def _grok(tmp_path, *answers):
    grok = mock.Mock()
    grok.extract_json.side_effect = [json.loads(json.dumps(a)) for a in answers]
    grok.cache_dir = tmp_path / "cache"
    grok.cache_key_for.side_effect = lambda p, s, t, temp: f"key:{hash(p)}"
    grok.clear_cache_entry.return_value = True
    grok.cache = {}
    grok._get_cache.return_value = grok.cache
    return grok


@pytest.fixture(autouse=True)
def _light(monkeypatch):
    saved = {}
    monkeypatch.setattr(E, "validate_event_json", lambda r: None)
    monkeypatch.setattr(E, "get_system_prompt", lambda n: "SYS")
    monkeypatch.setattr(
        E,
        "_save_event_output",
        lambda r, pf, od: saved.setdefault("out", (r, od / "out.json"))[1],
    )
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    return saved


def test_a_low_coverage_answer_is_rejected_evicted_kept_and_asked_again(
    tmp_path, _light
):
    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, LOW, FULL)
    out = E.extract_events(pf, grok, pf.parent, max_retries=3)
    assert out is not None and grok.extract_json.call_count == 2
    # the incomplete answer is evicted under the key it was cached with (system prompt included)
    first_prompt = grok.extract_json.call_args_list[0].kwargs["prompt"]
    grok.clear_cache_entry.assert_called_once_with(
        first_prompt, cache_type="events", system_prompt="SYS"
    )
    # the re-ask names what was left out, built on the original request
    second = grok.extract_json.call_args_list[1].kwargs["prompt"]
    assert second.startswith(first_prompt) and "4-20" in second
    assert "PREVIOUS ATTEMPT WAS INCOMPLETE" in second
    # the rejected answer is kept for diagnosis, under the book
    kept = json.loads(
        (
            tmp_path
            / "cache"
            / "rejected"
            / "B"
            / "chapter8full-parsed-coverage-attempt1.json"
        ).read_text()
    )
    assert kept["share"] == 0.15 and kept["uncovered"] == ["4-20"]
    # the complete answer is cached under the ORIGINAL request, so a re-run is served it
    assert grok.cache == {f"key:{hash(first_prompt)}": json.dumps(FULL)}
    saved, _ = _light["out"]
    assert len(saved["Event"]["Sub-events"]) == 2


def test_a_complete_first_answer_is_one_request_and_nothing_evicted(tmp_path):
    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, FULL)
    assert E.extract_events(pf, grok, pf.parent) is not None
    assert grok.extract_json.call_count == 1
    grok.clear_cache_entry.assert_not_called()
    assert grok.cache == {}


def test_coverage_at_the_threshold_passes(tmp_path):
    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, _answer(range(1, 17)))  # 16 of 20: 80%
    assert E.extract_events(pf, grok, pf.parent) is not None
    assert grok.extract_json.call_count == 1


def test_persistently_low_coverage_is_extracted_in_parts(tmp_path, monkeypatch):
    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, LOW, LOW)
    split = mock.Mock(return_value=pf.parent / "split.json")
    monkeypatch.setattr(E, "_auto_split_and_extract", split)
    assert E.extract_events(pf, grok, pf.parent, max_retries=2) == split.return_value
    assert split.call_args.kwargs["require_coverage"] is True


def test_when_the_parts_do_not_cover_it_either_the_chapter_fails_loudly(
    tmp_path, monkeypatch
):
    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, LOW, LOW)
    monkeypatch.setattr(E, "_auto_split_and_extract", mock.Mock(return_value=None))
    with mock.patch("src.utils.observe.observe_error") as alert:
        with pytest.raises(ValueError, match="cover only 15%"):
            E.extract_events(pf, grok, pf.parent, max_retries=2)
    assert alert.call_args.args[:2] == ("events", "coverage_unrecoverable")
    assert alert.call_args.kwargs["uncovered"] == ["4-20"]


def test_parts_that_still_leave_the_chapter_out_are_not_saved_and_are_evicted(
    tmp_path, monkeypatch
):
    pf = _parsed_file(tmp_path)
    parsed = json.loads(pf.read_text())
    grok = _grok(tmp_path)
    halves = [_answer(range(1, 3)), _answer(range(11, 12))]
    monkeypatch.setattr(
        E, "_extract_chunk", lambda pd, ch, i, g: json.loads(json.dumps(halves[i]))
    )
    for a in halves:  # what _extract_chunk would have normalised
        for se in a["Event"]["Sub-events"]:
            se["Sub-event_fulltext"] = {
                f"Paragraph_{n}": "t" for n in se.pop("paragraph_numbers")
            }
    out = E._auto_split_and_extract(parsed, pf, pf.parent, grok, require_coverage=True)
    assert out is None
    assert grok.clear_cache_entry.call_count == 2  # each part's cached answer


def test_with_the_cache_off_a_passing_retry_is_not_written(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_CACHE_DISABLED", "1")
    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, LOW, FULL)
    assert E.extract_events(pf, grok, pf.parent) is not None
    assert grok.cache == {}


def test_evicting_names_the_key_the_answer_was_stored_under(tmp_path, monkeypatch):
    """clear_cache_entry once hashed the prompt alone; extract_json keys on the system prompt and
    the model too, so every "retry with cache cleared" replayed the cached answer."""
    import src.grok_client as gc
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    grok = gc.GrokClient(DiskCacheBackend(tmp_path / "c"))
    reply = {"choices": [{"message": {"content": '{"ok": 1}'}}]}
    with mock.patch.object(grok, "_call_api", return_value=reply) as api:
        grok.extract_json(prompt="P", system_prompt="SYS", cache_type="events")
        assert grok.clear_cache_entry("P", cache_type="events", system_prompt="SYS")
        grok.extract_json(prompt="P", system_prompt="SYS", cache_type="events")
    assert api.call_count == 2  # evicted, so asked again
    assert grok.cache_key_for("P", "SYS", "events") in grok._get_cache("events")


def test_a_truncated_reask_of_an_under_covered_chapter_still_requires_coverage(
    tmp_path, monkeypatch
):
    from src.grok_client import GrokAPIError

    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, LOW)
    grok.extract_json.side_effect = [
        json.loads(json.dumps(LOW)),
        GrokAPIError("response truncated, splitting chapter"),
    ]
    split = mock.Mock(return_value=None)
    monkeypatch.setattr(E, "_auto_split_and_extract", split)
    with mock.patch("src.utils.observe.observe_error"):
        with pytest.raises(ValueError, match="cover only"):
            E.extract_events(pf, grok, pf.parent, max_retries=3)
    assert split.call_args.kwargs["require_coverage"] is True


def test_a_split_that_raises_still_raises_the_canonical_alert(tmp_path, monkeypatch):
    pf = _parsed_file(tmp_path)
    grok = _grok(tmp_path, LOW, LOW)
    monkeypatch.setattr(
        E,
        "_auto_split_and_extract",
        mock.Mock(side_effect=RuntimeError("chunk failed")),
    )
    with mock.patch("src.utils.observe.observe_error") as alert:
        with pytest.raises(RuntimeError, match="chunk failed"):
            E.extract_events(pf, grok, pf.parent, max_retries=2)
    assert alert.call_args.args[:2] == ("events", "coverage_unrecoverable")
    assert alert.call_args.kwargs["split_error"] == "RuntimeError"
