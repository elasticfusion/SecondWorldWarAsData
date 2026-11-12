"""The four entity requests of a chapter (dates, places, groups, people) send the chapter text as
their FIRST message, identical across the four, and their own instructions after it: xAI caches a
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
    """{cache_type: (system, prompt)} of the four entity requests for EVENT."""
    seen = {}

    class Client:
        def extract_json(
            self, prompt, system_prompt=None, cache_type="", book=None, **k
        ):
            seen[cache_type] = (system_prompt, prompt)
            raise gc.BatchModeCollecting("queued")

    async def run():
        root, meta = Path(tempfile.mkdtemp()), {"book": "B"}
        await bp.extract_dates_batch_async(EVENT, {}, Client(), root, meta)
        await bp.extract_places_batch_async(EVENT, {}, Client(), root, meta)
        await bp.extract_people_groups_batch_async(EVENT, Client(), root, meta)
        await bp.extract_people_batch_async(EVENT, Client(), root, meta)

    asyncio.run(run())
    return seen


def test_the_four_requests_share_one_first_message():
    reqs = _requests()
    assert set(reqs) == {"dates", "places", "peoplegroups", "people"}
    sources = {system for system, _ in reqs.values()}
    assert len(sources) == 1  # identical: the cacheable prefix
    source = sources.pop()
    assert "Force U was marshaled at Tor Bay." in source and "[S2]" in source
    for _system, prompt in reqs.values():
        assert "Tor Bay" not in prompt  # the chapter text is not repeated in the task
        assert prompt.rstrip().endswith("answer for every one of them.")
        assert not prompt.rstrip().endswith("Sub-events:")


def test_the_places_request_asks_no_coordinates():
    _system, prompt = _requests()["places"]
    assert "coordinates" not in prompt and "latitude" not in prompt


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
