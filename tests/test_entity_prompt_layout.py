"""The chapter entity requests (groups) send the chapter text as their FIRST message,
and their own instructions after it (places, dates and people: one request per
sub-event, each with its sub-event's text first): xAI caches a
request's leading messages (cached input $0.20 vs $1.25 / 1M tokens), routed by x-grok-conv-id.
And Phase 2 places carry no coordinates (Phase 3 geocodes): asking for them was a guess stored
with a made-up confidence, and the chapter-8 places answer stopped after 2 of 12 sub-events while
the same request without them answered all 12 (2026-11-12)."""

import asyncio
import tempfile
from pathlib import Path

import pytest

import src.grok_client as gc
from src.extraction import batch_parallel as bp

EVENT = {
    "Event": {
        "EventID": "E1",
        "Sub-events": [
            {
                "Sub-eventID": "S1",
                "Sub-event_summary": "Force U marshals",
                "Sub-event_fulltext": {
                    "Paragraph_1": "Force U was marshaled at Tor Bay."
                },
            },
            {
                "Sub-eventID": "S2",
                "Sub-event_summary": "Landing",
                "Sub-event_fulltext": {
                    "Paragraph_2": "The 4th Division landed at UTAH."
                },
            },
        ],
    }
}


def _requests():
    """{cache_type: [(system, prompt), ...]} of the entity requests for EVENT."""
    seen = {}

    class Client:
        def extract_json(
            self, prompt, system_prompt=None, cache_type="", book=None, **k
        ):
            seen.setdefault(cache_type, []).append((system_prompt, prompt))
            raise gc.BatchModeCollecting("queued")

    async def run():
        root, meta = Path(tempfile.mkdtemp()), {"book": "B"}
        await bp.extract_dates_batch_async(EVENT, {}, Client(), root, meta)
        await bp.extract_places_batch_async(EVENT, {}, Client(), root, meta)
        await bp.extract_people_groups_batch_async(EVENT, Client(), root, meta)
        await bp.extract_people_batch_async(EVENT, Client(), root, meta)

    asyncio.run(run())
    return seen


def test_the_chapter_requests_share_one_first_message():
    reqs = _requests()
    assert set(reqs) == {"dates", "places", "peoplegroups", "people"}
    chapter_types = ("peoplegroups",)
    assert all(len(reqs[t]) == 1 for t in chapter_types)
    sources = {reqs[t][0][0] for t in chapter_types}
    assert len(sources) == 1  # identical: the cacheable prefix
    source = sources.pop()
    assert "Force U was marshaled at Tor Bay." in source and "[S2]" in source
    for t in chapter_types:
        prompt = reqs[t][0][1]
        assert "Tor Bay" not in prompt  # the chapter text is not repeated in the task
        assert prompt.rstrip().endswith("answer for every one of them.")


@pytest.mark.parametrize(
    "entity, phrase",
    [
        ("places", "EVERY place named in this sub-event"),
        ("dates", "all dates and times from this sub-event"),
        ("people", "ALL people mentioned in this sub-event"),
    ],
)
def test_entities_are_asked_one_sub_event_at_a_time(entity, phrase):
    """Chapter-wide, the same places request returned 42, 118 and 89 places on three runs; per
    sub-event, three runs returned 227, 226 and 226 (chapter 8, 2026-11-12). Dates: 74 mentions
    chapter-wide, 91 / 98 / 99 per sub-event. People: 78 chapter-wide, 105 / 103 / 102 per
    sub-event - and chapter-wide named people the text does not ("Eugen König")."""
    reqs = _requests()[entity]
    assert len(reqs) == 2  # one per sub-event
    texts = [system for system, _ in reqs]
    assert any("Tor Bay" in t and "UTAH" not in t for t in texts)
    assert any("UTAH" in t and "Tor Bay" not in t for t in texts)
    for _system, prompt in reqs:
        assert phrase in prompt
        assert "coordinates" not in prompt and "latitude" not in prompt


def test_per_sub_event_requests_share_the_sub_event_first_message():
    """Places, dates and people for one sub-event lead with the same text: one cacheable prefix."""
    reqs = _requests()
    firsts = sorted(s for s, _ in reqs["places"])
    assert firsts == sorted(s for s, _ in reqs["dates"])
    assert firsts == sorted(s for s, _ in reqs["people"])


def test_per_sub_event_results_are_merged_and_a_stop_ends_it():
    from src.utils.grok_models import GrokModelUnavailable

    async def one(ev):
        se = ev["Event"]["Sub-events"]
        assert len(se) == 1
        sid = se[0]["Sub-eventID"]
        return {"count": 2, "links": {sid: ["P" + sid]}}

    merged = asyncio.run(bp._per_sub_event(one, EVENT))
    assert merged == {"count": 4, "links": {"S1": ["PS1"], "S2": ["PS2"]}, "failed": 0}

    done = []

    async def gone(ev):
        sid = ev["Event"]["Sub-events"][0]["Sub-eventID"]
        if sid == "S1":
            await asyncio.sleep(0)  # S2's request is sent before S1's answer arrives
            raise GrokModelUnavailable("x")
        done.append(sid)
        return {"count": 1, "links": {}}

    with pytest.raises(GrokModelUnavailable):
        asyncio.run(bp._per_sub_event(gone, EVENT))
    assert done == [
        "S2"
    ]  # the other sub-event's request was already in flight: it finished (records saved)


def test_after_a_model_stop_waiting_requests_are_not_sent(monkeypatch):
    from src.utils.grok_models import GrokModelUnavailable

    monkeypatch.setattr(bp, "_SUB_EVENT_CONCURRENCY", 1)
    sent = []

    async def gone(ev):
        sid = ev["Event"]["Sub-events"][0]["Sub-eventID"]
        sent.append(sid)
        raise GrokModelUnavailable("x")

    many = {
        "Event": {
            "EventID": "E",
            "Sub-events": [{"Sub-eventID": f"S{i}"} for i in range(5)],
        }
    }
    with pytest.raises(GrokModelUnavailable):
        asyncio.run(bp._per_sub_event(gone, many))
    assert sent == ["S0"]


def test_a_place_record_carries_no_coordinates():
    rec = bp._make_place_record(
        {
            "name": "Caen",
            "type": "city",
            "coordinates": {"latitude": 49.1, "longitude": -0.3},
        }
    )
    assert "coordinates" not in rec and "map_urls" not in rec
    assert rec["current_name"] == "Caen" and rec["geography_type"] == "city"


def test_requests_sharing_a_first_message_share_a_cache_route():
    src_x = gc.SOURCE_TEXT_INTRO + " X"
    src_y = gc.SOURCE_TEXT_INTRO + " Y"
    a = gc._cache_routing_header(
        [{"role": "system", "content": src_x}, {"role": "user", "content": "1"}]
    )
    b = gc._cache_routing_header(
        [{"role": "system", "content": src_x}, {"role": "user", "content": "2"}]
    )
    c = gc._cache_routing_header([{"role": "system", "content": src_y}])
    assert a == b and a != c and a["x-grok-conv-id"].startswith("src-")
    assert gc._cache_routing_header([{"role": "user", "content": src_x}]) == {}
    assert (
        gc._cache_routing_header([{"role": "system", "content": "You geocode."}]) == {}
    )


def test_the_cached_prompt_tokens_are_logged(caplog):
    import logging

    client = object.__new__(gc.GrokClient)
    client.model = "grok-4.3"
    with caplog.at_level(logging.INFO, logger="src.grok_client"):
        client._log_token_usage(
            {
                "model": "grok-4.3",
                "usage": {
                    "prompt_tokens": 1000,
                    "prompt_tokens_details": {"cached_tokens": 900},
                    "completion_tokens": 10,
                    "total_tokens": 1010,
                },
            }
        )
    assert "1000 prompt (900 cached)" in caplog.text


@pytest.mark.parametrize("include_fulltext", [True, False])
def test_the_task_points_at_the_source(include_fulltext):
    source, task = bp._build_batch_prompt(
        "Extract all X. Return JSON: {}\n\nSub-events:",
        EVENT["Event"]["Sub-events"],
        include_fulltext,
    )
    assert source.startswith(bp._SOURCE_INTRO)
    assert task.startswith("Extract all X. Return JSON: {}")
    assert ("Tor Bay" in source) is include_fulltext


# --- review follow-ups ---------------------------------------------------------------------------


def _client(tmp_path, monkeypatch, batch_mode=False):
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_MODEL", raising=False)
    return gc.GrokClient(DiskCacheBackend(tmp_path), batch_mode=batch_mode)


def _reply(content='{"x": 1}'):
    from types import SimpleNamespace

    body = {
        "model": "grok-4.3",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }
    return SimpleNamespace(status_code=200, json=lambda: body, text="")


def test_the_routing_header_is_sent_only_for_a_chapter_source(tmp_path, monkeypatch):
    from unittest import mock

    client = _client(tmp_path, monkeypatch)
    source = bp._build_source_block(EVENT["Event"]["Sub-events"], True)
    with mock.patch.object(
        client, "_post_with_deadline", return_value=_reply()
    ) as post:
        client._call_api(
            [{"role": "system", "content": source}, {"role": "user", "content": "t"}]
        )
        client._call_api(
            [
                {"role": "system", "content": "You geocode."},
                {"role": "user", "content": "t"},
            ]
        )
    sent = [c.args[1] for c in post.call_args_list]
    assert "x-grok-conv-id" in sent[0] and "x-grok-conv-id" not in sent[1]


def test_the_size_cap_counts_the_chapter_text(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="too large"):
        client._validate_prompt("task", "x" * 500_001)


def test_a_batch_request_carries_source_and_task_and_its_key_is_the_re_runs(
    tmp_path, monkeypatch
):
    """Submit and retrieve re-run must name the same request: [system=source, user=task]."""
    client = _client(tmp_path, monkeypatch, batch_mode=True)
    source = bp._build_source_block(EVENT["Event"]["Sub-events"], True)
    with gc.book_scope("B"):
        with pytest.raises(gc.BatchModeCollecting):
            client.extract_json(
                "task", system_prompt=source, cache_type="people", book="B"
            )
    queued = client._batch_collector.requests[-1]
    assert [m["role"] for m in queued.messages] == ["system", "user"]
    assert queued.messages[0]["content"] == source
    assert queued.request_id == client.cache_key_for("task", source, "people", book="B")


def _geocode_dir(tmp_path, place, result):
    import json

    from src.enrichment import places_grok_geocode as pgg

    places = tmp_path / "places"
    places.mkdir()
    (places / "p.json").write_text(json.dumps(place))
    pgg.geocode_places_dir(
        places, None, write=True, geocoder=lambda name, p, client: result
    )
    return json.loads((places / "p.json").read_text())


GUESS = {
    "PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01",
    "current_name": "Colleville",
    "coordinates": {
        "latitude": 49.3,
        "longitude": -0.8,
        "precision": "approximate",
        "confidence": 0.8,
    },
    "latitude": 49.3,
    "longitude": -0.8,
    "map_urls": {"osm": "x"},
}


def test_a_guess_the_geocoder_cannot_confirm_is_dropped(tmp_path):
    from src.enrichment.places_grok_geocode import GeocodeResult
    from src.enrichment.places_geo import coords_verified

    out = _geocode_dir(
        tmp_path,
        dict(GUESS),
        GeocodeResult(found=False, confidence=0.0, source="nominatim"),
    )
    assert "coordinates" not in out and "map_urls" not in out and "latitude" not in out
    assert not coords_verified(out)  # never reads as a real geocode


def test_a_confirmed_guess_becomes_a_geocode(tmp_path):
    from src.enrichment.places_grok_geocode import GeocodeResult
    from src.enrichment.places_geo import coords_verified

    hit = GeocodeResult(
        found=True, latitude=49.34, longitude=-0.85, confidence=0.95, source="nominatim"
    )
    out = _geocode_dir(tmp_path, dict(GUESS), hit)
    assert out["coordinates"]["latitude"] == 49.34 and coords_verified(out)


def test_stated_exact_coordinates_are_not_re_geocoded():
    from src.enrichment.places_grok_geocode import _needs_geocode

    exact = dict(
        GUESS, coordinates={"latitude": 49.3, "longitude": -0.8, "precision": "exact"}
    )
    assert not _needs_geocode(exact)
    assert _needs_geocode(dict(GUESS))


# --- per-sub-event review follow-ups ------------------------------------------------------------


def test_a_failed_sub_event_request_is_counted_and_alerted(monkeypatch):
    from unittest import mock

    async def one(ev):
        sid = ev["Event"]["Sub-events"][0]["Sub-eventID"]
        if sid == "S1":
            return {"count": 0, "links": {}, "failed": 1}
        return {"count": 3, "links": {sid: ["P1"]}}

    with mock.patch.object(bp, "observe_error") as err:
        merged = asyncio.run(bp._per_sub_event(one, EVENT, "places"))
    assert merged["count"] == 3 and merged["failed"] == 1
    assert err.call_args.args[:2] == ("places", "sub_events_failed")
    assert err.call_args.kwargs["failed"] == 1 and err.call_args.kwargs["of"] == 2


def test_a_request_failure_inside_batch_extract_is_marked(tmp_path):
    class Broken:
        def extract_json(self, *a, **k):
            raise RuntimeError("api down")

    ev = {"Event": {"EventID": "E1", "Sub-events": [EVENT["Event"]["Sub-events"][0]]}}
    out = asyncio.run(bp._extract_places(ev, Broken(), tmp_path, {}))
    assert out["failed"] == 1 and out["count"] == 0


def test_no_sub_events_is_an_empty_result():
    async def one(ev):
        raise AssertionError("not called")

    merged = asyncio.run(bp._per_sub_event(one, {"Event": {"Sub-events": []}}))
    assert merged == {"count": 0, "links": {}, "failed": 0}


def test_a_non_dict_result_is_an_error():
    async def one(ev):
        return None

    with pytest.raises(TypeError):
        asyncio.run(bp._per_sub_event(one, EVENT, "places"))


def test_a_raised_failure_is_counted_and_the_others_keep_their_links():
    from unittest import mock

    async def one(ev):
        sid = ev["Event"]["Sub-events"][0]["Sub-eventID"]
        if sid == "S1":
            raise KeyError("boom")
        return {"count": 1, "links": {sid: ["P1", "P1"]}}

    with mock.patch.object(bp, "observe_error") as err:
        merged = asyncio.run(bp._per_sub_event(one, EVENT, "places"))
    assert merged == {"count": 1, "links": {"S2": ["P1"]}, "failed": 1}
    assert "S1: KeyError" in err.call_args.kwargs["errors"][0]


def test_sub_event_requests_are_capped(monkeypatch):
    monkeypatch.setattr(bp, "_SUB_EVENT_CONCURRENCY", 2)
    in_flight, peak = [0], [0]

    async def one(ev):
        in_flight[0] += 1
        peak[0] = max(peak[0], in_flight[0])
        await asyncio.sleep(0.01)
        in_flight[0] -= 1
        return {"count": 1, "links": {}}

    many = {
        "Event": {
            "EventID": "E",
            "Sub-events": [{"Sub-eventID": f"S{i}"} for i in range(7)],
        }
    }
    for _ in range(2):  # a second event loop gets its own semaphore, still capped
        peak[0] = 0
        merged = asyncio.run(bp._per_sub_event(one, many))
        assert merged["count"] == 7 and peak[0] == 2


def test_place_mentions_link_dates_written_while_the_request_ran(tmp_path):
    """Dates are extracted concurrently: the lookup must be read after the answer, not before."""
    from unittest import mock

    seen = []

    class Client:
        def extract_json(self, *a, **k):
            (tmp_path / "dates").mkdir(exist_ok=True)
            (tmp_path / "dates" / "d.json").write_text("{}")  # written mid-request
            return {"places": []}

    def lookup(d):
        seen.append(sorted(p.name for p in d.glob("*.json")))
        return {}

    ev = {"Event": {"EventID": "E1", "Sub-events": [EVENT["Event"]["Sub-events"][0]]}}
    with (
        mock.patch.object(bp, "_build_date_id_lookup", side_effect=lookup),
        mock.patch.object(
            bp, "_process_batch_response", return_value={"count": 0, "links": {}}
        ),
    ):
        asyncio.run(bp.extract_places_batch_async(ev, {}, Client(), tmp_path, {}))
    assert seen == [["d.json"]]


def test_one_date_in_two_sub_events_is_one_record_with_two_mentions(tmp_path):
    """Per-sub-event dates requests write concurrently: a shared date must merge, not duplicate."""
    import json

    s1, s2 = "01HZZZZZZZZZZZZZZZZZZZZZZ1", "01HZZZZZZZZZZZZZZZZZZZZZZ2"
    ev = json.loads(json.dumps(EVENT))
    ev["Event"]["EventID"] = "01HZZZZZZZZZZZZZZZZZZZZZZ0"
    ev["Event"]["Sub-events"][0]["Sub-eventID"] = s1
    ev["Event"]["Sub-events"][1]["Sub-eventID"] = s2

    class Client:
        def extract_json(self, prompt, system_prompt=None, **k):
            sid = s1 if "Tor Bay" in (system_prompt or "") else s2
            return {
                "dates": [
                    {
                        "sub_event_id": sid,
                        "dates": [
                            {"date_start": "1944-06-06", "original_text": "D-Day"}
                        ],
                    }
                ]
            }

    asyncio.run(bp.extract_dates_batch_async(ev, {}, Client(), tmp_path, {"book": "B"}))
    records = [
        json.loads(f.read_text())
        for f in (tmp_path / "dates").glob("*.json")
        if f.name != "index.json"
    ]
    assert len(records) == 1
    seids = sorted(m["Sub_eventID"] for m in records[0]["event_mentions"])
    assert seids == [s1, s2]


def test_people_chapter_setup_and_summary_run_once(tmp_path):
    """Exclusions, the enrichment counters and their summary are per chapter, not per sub-event."""
    from unittest import mock

    from src.extraction import people as people_mod

    class Client:
        def extract_json(self, *a, **k):
            return {"people": []}

    with (
        mock.patch.object(people_mod, "_reset_enrich_counts") as reset,
        mock.patch.object(people_mod, "_emit_enrich_summary") as summary,
        mock.patch.object(bp, "_load_people_exclusions") as excl,
    ):
        out = asyncio.run(bp.extract_people_batch_async(EVENT, Client(), tmp_path, {}))
    assert reset.call_count == summary.call_count == excl.call_count == 1
    assert out["failed"] == 0
