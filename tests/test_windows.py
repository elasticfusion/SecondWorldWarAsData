"""People, groups, places and equipment are discovered in fixed paragraph windows of the parsed
chapter - raw text, no Grok summary, no dependence on the events answer for the REQUEST - and each
answer is attributed to the real sub-event holding the paragraph its quote came from."""

import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest

import src.grok_client as gc
from src.extraction import batch_parallel as bp
from src.extraction import windows as W

S1, S2 = "01HZZZZZZZZZZZZZZZZZZZZZS1", "01HZZZZZZZZZZZZZZZZZZZZZS2"
PARSED = {
    "book": "B",
    "author": "A",
    "chapter_title": "The Sixth of June",
    "paragraphs": [
        {"absolute_number": 1, "text": "Gerow landed at Omaha."},
        {"absolute_number": 2, "text": "The 16th Infantry crossed the beach."},
        {"absolute_number": 3, "text": "Bradley watched from the Augusta."},
        {"absolute_number": 4, "text": "Rudder climbed Pointe du Hoe."},
        {"absolute_number": 5, "text": "### Footnotes"},
        {"absolute_number": 6, "text": "[1] A note."},
    ],
}
# the real events answer: paragraphs 1-2 in S1, 3-4 in S2 (a window 1-3 straddles them)
REAL = {
    "Chapter": "The Sixth of June",
    "Event": {
        "EventID": "01HZZZZZZZZZZZZZZZZZZZZZE0",
        "Sub-events": [
            {
                "Sub-eventID": S1,
                "Sub-event_summary": "SUMMARY-S1 Omaha",
                "Sub-event_fulltext": {"Paragraph_001": "x", "Paragraph_002": "x"},
            },
            {
                "Sub-eventID": S2,
                "Sub-event_summary": "SUMMARY-S2 Pointe du Hoe",
                "Sub-event_fulltext": {"Paragraph_003": "x", "Paragraph_004": "x"},
            },
        ],
    },
}


def test_windows_are_the_narrative_paragraphs_in_fixed_groups():
    wev = W.window_event(PARSED, 3)
    wins = wev["Event"]["Sub-events"]
    assert [w["Sub-eventID"] for w in wins] == [
        "P001-P003",
        "P004-P004",
    ]  # footnotes left out
    assert list(wins[0]["Sub-event_fulltext"]) == [
        "Paragraph_001",
        "Paragraph_002",
        "Paragraph_003",
    ]
    assert all(
        w["Sub-event_summary"] == "" for w in wins
    )  # no Grok text, only the source
    assert W.window_event(PARSED, 3) == wev  # the same request whenever it is built


def test_the_window_event_id_is_the_chapters_event_id():
    from src.extraction.events import _apply_deterministic_ids

    real = _apply_deterministic_ids({"Event": {"Sub-events": []}}, PARSED)
    assert W.window_event(PARSED, 3)["Event"]["EventID"] == real["Event"]["EventID"]


def test_an_item_goes_to_the_sub_event_holding_its_quote():
    owners = W.paragraph_owners(REAL)
    window = W.window_event(PARSED, 3)["Event"]["Sub-events"][0]  # paragraphs 1-3
    assert W.attribute(window, {"original_text": "Bradley watched"}, owners) == (
        S2,
        [3],
        True,
    )
    assert W.attribute(window, {"original_text": "Gerow  landed"}, owners) == (
        S1,
        [1],
        True,
    )
    # quote not in any one paragraph: the window's first owned paragraph
    assert W.attribute(window, {"original_text": "elsewhere"}, owners) == (
        S1,
        [1, 2, 3],
        False,
    )
    assert (
        W.attribute(window, {"original_text": "x"}, {})[0] is None
    )  # no sub-event holds it


def test_a_window_answer_is_regrouped_under_the_real_sub_events():
    window = W.window_event(PARSED, 3)["Event"]["Sub-events"][0]
    answer = {
        "places": [
            {
                "sub_event_id": "garbled",  # one window per request: the echo is not needed
                "places": [
                    {"name": "Omaha", "original_text": "Gerow landed at Omaha."},
                    {"name": "Augusta", "original_text": "watched from the Augusta"},
                ],
                "relationships": [
                    {"from": "Omaha", "to": "Normandy", "type": "part_of"}
                ],
            }
        ]
    }
    out = W.regroup(answer, "places", "places", window, REAL, "places")
    by = {g["sub_event_id"]: [p["name"] for p in g["places"]] for g in out["places"]}
    assert by == {S1: ["Omaha"], S2: ["Augusta"]}
    assert out["places"][0]["relationships"][0]["to"] == "Normandy"  # kept


class Client:
    """Answers people requests from the window text it is sent; records every request."""

    def __init__(self, collect=False):
        self.collect, self.asked = collect, []

    def extract_json(self, prompt, system_prompt=None, cache_type="", book=None, **_k):
        self.asked.append((cache_type, system_prompt, prompt))
        if self.collect:
            raise gc.BatchModeCollecting("queued")
        people = [
            {"name": n, "original_text": q}
            for n, q in (
                ("Gerow", "Gerow landed at Omaha."),
                ("Bradley", "Bradley watched"),
            )
            if q.split()[0] in (system_prompt or "")
        ]
        return {"people": [{"sub_event_id": "x", "people": people}]}


@pytest.fixture
def isolated_people(monkeypatch):
    """Fresh people-exclusion state (module globals), restored after the test."""
    monkeypatch.setattr(bp, "_people_exclusions_file", None)
    monkeypatch.setattr(bp, "_people_exclusion_patterns", [])


def test_people_found_in_windows_get_the_real_sub_events(
    tmp_path, isolated_people, monkeypatch
):
    from src.extraction import people as people_mod

    # enrichment (Grokipedia/Wikipedia + a Grok call) is not what this tests
    monkeypatch.setattr(people_mod, "_enrich_person_batch_apply", lambda *a, **k: None)
    wev = W.window_event(PARSED, 3)
    client = Client()
    out = asyncio.run(
        bp.extract_people_batch_async(
            REAL, client, tmp_path, {"book": "B"}, windows_event=wev
        )
    )
    asked = [a for a in client.asked if a[0] == "people"]
    assert len(asked) == 2  # one request per window, not per sub-event
    assert not any(
        "SUMMARY-" in s + p for _c, s, p in asked
    )  # the events' own text never asked
    mentions = {
        json.loads(f.read_text())["name"]: [
            m["Sub_eventID"] for m in json.loads(f.read_text())["event_mentions"]
        ]
        for f in (tmp_path / "people").glob("*.json")
        if f.name != "index.json"
    }
    assert mentions == {"Gerow": [S1], "Bradley": [S2]}
    assert set(out["links"]) == {S1, S2}  # written into the real sub-events


def test_the_prefetch_asks_exactly_what_the_later_pass_asks(tmp_path, isolated_people):
    wev = W.window_event(PARSED, 3)
    early, later = Client(collect=True), Client(collect=True)
    asyncio.run(
        bp.extract_people_batch_async(
            None, early, tmp_path, {"book": "B"}, windows_event=wev, ask_only=True
        )
    )
    asyncio.run(
        bp.extract_people_batch_async(
            REAL, later, tmp_path, {"book": "B"}, windows_event=wev
        )
    )
    assert early.asked == later.asked and early.asked  # cache hits in the later pass


def test_a_chapter_without_events_asks_its_windows_with_the_events_request(tmp_path):
    book = tmp_path / "content" / "B"
    book.mkdir(parents=True)
    parsed_file = book / "chapter8full-parsed.json"
    parsed_file.write_text(json.dumps(PARSED))
    client = Client(collect=True)
    events = mock.Mock(side_effect=gc.BatchModeCollecting("events queued"))
    with (
        mock.patch("src.extraction.events.extract_events", events),
        mock.patch.object(bp, "_people_exclusions_file", None),
        pytest.raises(gc.BatchModeCollecting),
    ):
        asyncio.run(
            bp.process_chapter_async(
                parsed_file,
                book / "chapter8full-event.json",
                client,
                tmp_path,
                {"equipment": {"enabled": False}},
            )
        )
    kinds = sorted({c for c, _s, _p in client.asked})
    assert kinds == [
        "dates",
        "people",
        "peoplegroups",
        "places",
    ]  # queued in the events' round
    assert len(client.asked) == 4 * 2  # four types x two windows


def test_equipment_discovery_runs_over_the_windows_and_the_real_event_attributes(
    monkeypatch,
):
    from src.extraction import equipment as eq

    wev = W.window_event(PARSED, 3)
    asked = []

    def broad_one(se, view, client):
        asked.append(se["Sub-eventID"])
        item = {
            "name": "Augusta",
            "original_text": "the Augusta",
            "paragraph_numbers": [3],
        }
        return (
            [eq._pin_to_sub_event(item, se)] if se["Sub-eventID"] == "P001-P003" else []
        )

    monkeypatch.setattr(eq, "_extract_equipment_broad_one", broad_one)
    monkeypatch.setattr(eq, "_refine_or_fallback", lambda item, ev, c: dict(item))
    items = eq._extract_equipment_with_llm(REAL, None, 1, windows_event=wev)
    assert sorted(asked) == ["P001-P003", "P004-P004"]
    (item,) = items
    assert item["paragraph_numbers"] == [3]
    assert eq._find_sub_event(REAL, item["paragraph_numbers"])["Sub-eventID"] == S2


def test_window_size_env_then_config_then_default(monkeypatch):
    W._configured_size.cache_clear()
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {"extraction": {"window_paragraphs": 5}},
    )
    monkeypatch.delenv("WINDOW_PARAGRAPHS", raising=False)
    try:
        assert W.window_size() == 5
        monkeypatch.setenv("WINDOW_PARAGRAPHS", "2")
        assert W.window_size() == 2
        monkeypatch.setenv("WINDOW_PARAGRAPHS", "two")
        assert W.window_size() == W.DEFAULT_WINDOW_PARAGRAPHS
    finally:
        W._configured_size.cache_clear()


def test_an_unreadable_config_gives_the_default_window(monkeypatch):
    W._configured_size.cache_clear()

    def boom(*_a, **_k):
        raise OSError("no config")

    monkeypatch.setattr("src.utils.config.load_config", boom)
    monkeypatch.delenv("WINDOW_PARAGRAPHS", raising=False)
    try:
        assert W.window_size() == W.DEFAULT_WINDOW_PARAGRAPHS
    finally:
        W._configured_size.cache_clear()


def test_nothing_is_asked_when_windows_cannot_be_attributed(tmp_path, isolated_people):
    wev = W.window_event(PARSED, 3)
    no_windows = {"Event": {"EventID": "E", "Sub-events": []}}
    no_events = {"Event": {"EventID": "E", "Sub-events": []}}
    client = Client()
    with mock.patch.object(bp, "observe_error") as loud:
        a = asyncio.run(
            bp.extract_places_batch_async(
                REAL, PARSED, client, tmp_path, {}, windows_event=no_windows
            )
        )
        loud.assert_called_once()  # a chapter with sub-events but no windows is a FAILURE
        assert loud.call_args.args[1] == "windows_empty"
        b = asyncio.run(
            bp.extract_people_groups_batch_async(
                no_events, client, tmp_path, {}, windows_event=wev
            )
        )
    assert client.asked == []  # never a fall back to the events' own text
    assert a["count"] == b["count"] == 0


def test_a_bad_parsed_file_never_fails_the_prefetch(tmp_path):
    parsed_file = tmp_path / "x-parsed.json"
    parsed_file.write_text(json.dumps(["not", "a", "chapter"]))
    client = Client()
    asyncio.run(bp.prefetch_windows(parsed_file, client, tmp_path, {}))
    asyncio.run(bp.prefetch_windows(tmp_path / "missing.json", client, tmp_path, {}))
    assert client.asked == []


def test_equipment_asks_nothing_for_a_chapter_without_windows(monkeypatch):
    from src.extraction import equipment as eq

    broad = mock.Mock()
    monkeypatch.setattr(eq, "_extract_equipment_broad", broad)
    empty = {"Event": {"EventID": "E", "Sub-events": []}}
    assert eq._extract_equipment_with_llm(REAL, None, 1, windows_event=empty) == []
    broad.assert_not_called()  # not the whole-event ask


def test_the_prefetch_never_masks_the_events_exception(tmp_path):
    from src.utils.grok_models import GrokModelUnavailable

    parsed_file = tmp_path / "c-parsed.json"
    parsed_file.write_text(json.dumps(PARSED))

    async def gone(*_a, **_k):
        raise GrokModelUnavailable("model gone")

    def run(events_exc):
        with (
            mock.patch(
                "src.extraction.events.extract_events",
                mock.Mock(side_effect=events_exc),
            ),
            mock.patch.object(bp, "prefetch_windows", gone),
        ):
            asyncio.run(
                bp.process_chapter_async(
                    parsed_file, tmp_path / "c-event.json", Client(), tmp_path, {}
                )
            )

    with pytest.raises(RuntimeError):  # the events' own failure stands
        run(RuntimeError("events broke"))
    with pytest.raises(GrokModelUnavailable):  # queued events + a gone model: stop
        run(gc.BatchModeCollecting("queued"))


def test_a_malformed_parsed_file_gives_no_windows(tmp_path):
    (tmp_path / "c-parsed.json").write_text(json.dumps(["not", "a", "chapter"]))
    assert W.window_event_for(tmp_path / "c-event.json", 3) is None


def test_dates_found_in_windows_get_the_real_sub_events(tmp_path):
    asked = []

    class Dates:
        def extract_json(self, prompt, system_prompt=None, cache_type="", **_k):
            asked.append((cache_type, system_prompt or ""))
            found = [
                {"date_start": "1944-06-06", "original_text": "Gerow landed at Omaha."}
            ]
            if "Gerow" not in (system_prompt or ""):
                found = [
                    {"date_start": "1944-06-07", "original_text": "Rudder climbed"}
                ]
            return {"dates": [{"sub_event_id": "x", "dates": found}]}

    out = asyncio.run(
        bp.extract_dates_batch_async(
            REAL,
            PARSED,
            Dates(),
            tmp_path,
            {"book": "B"},
            windows_event=W.window_event(PARSED, 3),
        )
    )
    assert len(asked) == 2 and not any("SUMMARY-" in s for _c, s in asked)
    by_date = {
        json.loads(f.read_text())["date_start"]: [
            m["Sub_eventID"] for m in json.loads(f.read_text())["event_mentions"]
        ]
        for f in (tmp_path / "dates").glob("*.json")
        if f.name != "index.json"
    }
    assert by_date == {"1944-06-06": [S1], "1944-06-07": [S2]}
    assert set(out["links"]) == {S1, S2}


def _weather_chapter(tmp_path, parsed):
    book = tmp_path / "content" / "B"
    book.mkdir(parents=True)
    event_file = book / "c-event.json"
    event_file.write_text(json.dumps(REAL))
    parsed_file = book / "c-parsed.json"
    parsed_file.write_text(json.dumps(parsed))
    (tmp_path / "weather").mkdir()
    return event_file, parsed_file


def test_weather_is_asked_per_window_of_raw_text_and_attributed_by_quote(
    tmp_path, monkeypatch
):
    from src.extraction import weather_central as wc

    monkeypatch.setattr("src.utils.config.should_reprocess", lambda *_a: False)
    event_file, parsed_file = _weather_chapter(tmp_path, PARSED)
    prompts = []

    class Weather:
        batch_mode = False

        def extract_json(self, prompt, **_k):
            prompts.append(prompt)
            if "[P001-P003]" in prompt:
                return {
                    "P001-P003": [
                        {
                            "place_name": "Augusta",
                            "date": "1944-06-06",
                            "weather_description": "fair from the Augusta",
                            "original_text": "Bradley watched from the Augusta.",
                        }
                    ]
                }
            return {}

    wc.extract_weather_central(
        event_file,
        tmp_path / "weather",
        Weather(),
        tmp_path / "places",
        parsed_file=parsed_file,
    )
    assert len(prompts) == 2  # one request per window
    assert not any("SUMMARY-" in p or "Available Places" in p for p in prompts)
    (rec,) = [
        json.loads(f.read_text())
        for f in (tmp_path / "weather").glob("*.json")
        if f.name not in ("index.json", ".processed_events.json")
    ]
    # paragraph 3 is S2's (the window P001-P003 straddles S1 and S2)
    assert [m["Sub_eventID"] for m in rec["event_mentions"]] == [S2]


def test_weather_asks_nothing_for_a_chapter_without_windows(tmp_path, monkeypatch):
    from src.extraction import weather_central as wc

    monkeypatch.setattr("src.utils.config.should_reprocess", lambda *_a: False)
    event_file, parsed_file = _weather_chapter(
        tmp_path, {"book": "B", "author": "A", "paragraphs": []}
    )
    client = mock.Mock()
    with mock.patch("src.utils.observe.observe_error") as loud:
        assert (
            wc.extract_weather_central(
                event_file,
                tmp_path / "weather",
                client,
                tmp_path / "places",
                parsed_file=parsed_file,
            )
            is None
        )
    client.extract_json.assert_not_called()  # never the events' own text
    assert loud.call_args.args[:2] == ("weather", "windows_empty")


def test_a_window_answer_under_a_mangled_key_is_still_the_windows(
    tmp_path, monkeypatch
):
    from src.extraction import weather_central as wc
    from src.utils.quote_match import QUOTE_RULE

    monkeypatch.setattr("src.utils.config.should_reprocess", lambda *_a: False)
    event_file, parsed_file = _weather_chapter(tmp_path, PARSED)
    prompts = []

    class Mangled:
        batch_mode = False

        def extract_json(self, prompt, **_k):
            prompts.append(prompt)
            if "[P004-P004]" not in prompt:
                return {}
            return {
                "window 2": [  # not the window id
                    {
                        "place_name": "Pointe du Hoe",
                        "date": "1944-06-06",
                        "weather_description": "rough surf",
                        "original_text": "Rudder climbed Pointe du Hoe.",
                    }
                ]
            }

    wc.extract_weather_central(
        event_file,
        tmp_path / "weather",
        Mangled(),
        tmp_path / "places",
        parsed_file=parsed_file,
    )
    assert all(QUOTE_RULE in p for p in prompts)  # the shared quote rule
    (rec,) = [
        json.loads(f.read_text())
        for f in (tmp_path / "weather").glob("*.json")
        if f.name not in ("index.json", ".processed_events.json")
    ]
    assert [m["Sub_eventID"] for m in rec["event_mentions"]] == [S2]
