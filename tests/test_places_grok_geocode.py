"""Tests for Grok-backed place geocoding (write-back, confidence gating, idempotency).

Uses a fake Grok client — no network calls. Verifies that a confident hit writes
nested coordinates + derived fields, that low-confidence/not-found results are
recorded without guessing coordinates, and that runs are idempotent and bounded.
"""

import json

import pytest
from pathlib import Path

from src.enrichment.places_grok_geocode import (
    GeocodeResult,
    geocode_places_dir,
)


class _FakeGrok:
    """Returns a scripted GeocodeResult per place name (as a JSON reply)."""

    def __init__(self, answers):
        self._answers = answers
        self.calls = []

    def chat_completion(
        self, prompt, system_prompt=None, temperature=0.1, cache_type=None
    ):
        # Identify which place by matching a name in the prompt.
        for name, result in self._answers.items():
            if repr(name) in prompt or name in prompt:
                self.calls.append(name)
                return result.model_dump_json()
        self.calls.append("<unknown>")
        return GeocodeResult(found=False, confidence=0.0).model_dump_json()


def _place_file(d: Path, fname: str, obj: dict) -> Path:
    p = d / fname
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_confident_hit_writes_coordinates_and_derived(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "aachen.json",
        {"PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01", "current_name": "Aachen"},
    )
    grok = _FakeGrok(
        {
            "Aachen": GeocodeResult(
                found=True,
                latitude=50.78,
                longitude=6.08,
                country="Germany",
                confidence=0.9,
            )
        }
    )
    report = geocode_places_dir(places, grok, write=True)
    assert report.geocoded == 1
    saved = json.loads((places / "aachen.json").read_text())
    assert saved["coordinates"]["latitude"] == 50.78
    assert saved["country"] == "Germany"
    assert "bounding_box" in saved and "map_urls" in saved
    assert saved["enrichment_status"] == "geocoded"
    assert saved["geocode_source"] == "grok"  # provenance recorded


def test_low_confidence_not_written_as_coordinate(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "hill.json",
        {"PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01", "current_name": "Hill 401"},
    )
    grok = _FakeGrok(
        {
            "Hill 401": GeocodeResult(
                found=True,
                latitude=1.0,
                longitude=2.0,
                confidence=0.3,
                note="ambiguous",
            )
        }
    )
    report = geocode_places_dir(places, grok, write=True)
    assert report.low_confidence == 1
    saved = json.loads((places / "hill.json").read_text())
    assert not saved.get("coordinates")  # no guessed coordinate
    assert saved["enrichment_status"] == "geocode_low_confidence"


def test_not_found_recorded(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "x.json",
        {"PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01", "current_name": "Monunirel"},
    )
    grok = _FakeGrok({"Monunirel": GeocodeResult(found=False, confidence=0.0)})
    report = geocode_places_dir(places, grok, write=True)
    assert report.not_found == 1
    saved = json.loads((places / "x.json").read_text())
    assert saved["enrichment_status"] == "geocode_not_found"


def test_unit_area_names_skipped(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "sector.json",
        {
            "PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01",
            "current_name": "5th Division sector",
        },
    )
    grok = _FakeGrok({})
    report = geocode_places_dir(places, grok, write=True)
    assert report.attempted == 0
    assert report.skipped == 1
    assert grok.calls == []  # never called Grok for a unit area


def test_already_geocoded_skipped(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "metz.json",
        {
            "PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01",
            "current_name": "Metz",
            "coordinates": {"latitude": 49.12, "longitude": 6.18},
            "geocode_source": "nominatim",  # what a Phase-3 geocode records
        },
    )
    grok = _FakeGrok({})
    report = geocode_places_dir(places, grok, write=True)
    assert report.attempted == 0
    assert report.skipped == 1


def test_a_phase2_guessed_coordinate_is_geocoded(tmp_path: Path) -> None:
    """Phase-2 coordinates were the extraction model's guess (no geocode_source, a made-up
    confidence 0.8): Phase 3 must geocode the place, not skip it as already placed."""
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "metz.json",
        {
            "PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01",
            "current_name": "Metz",
            "coordinates": {
                "latitude": 49.1,
                "longitude": 6.2,
                "precision": "approximate",
                "confidence": 0.8,
            },
        },
    )
    report = geocode_places_dir(places, _FakeGrok({}), write=True)
    assert report.attempted == 1 and report.skip_has_coords == 0


def test_idempotent_after_attempt(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "x.json",
        {"PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ01", "current_name": "Monunirel"},
    )
    grok = _FakeGrok({"Monunirel": GeocodeResult(found=False, confidence=0.0)})
    geocode_places_dir(places, grok, write=True)
    # Second run should skip (already attempted), making no new Grok calls.
    grok.calls.clear()
    report2 = geocode_places_dir(places, grok, write=True)
    assert report2.attempted == 0
    assert grok.calls == []


def test_limit_bounds_calls(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    for i in range(5):
        _place_file(
            places, f"p{i}.json", {"PlaceID": f"0{i}", "current_name": f"Town{i}"}
        )
    grok = _FakeGrok(
        {
            f"Town{i}": GeocodeResult(
                found=True, latitude=50.0, longitude=6.0, confidence=0.9
            )
            for i in range(5)
        }
    )
    report = geocode_places_dir(places, grok, write=False, limit=2)
    assert report.attempted == 2


def test_error_recorded_and_batch_continues(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(
        places,
        "a.json",
        {"PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ00", "current_name": "Aachen"},
    )
    _place_file(
        places,
        "b.json",
        {"PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZ00", "current_name": "Metz Town"},
    )

    class _Boom:
        def chat_completion(self, **_):
            raise RuntimeError("api down")

    report = geocode_places_dir(places, _Boom(), write=False)
    # both attempted, no crash; a Grok error is transient (task 1.2f): left retriable, never
    # stored as geocode_not_found
    assert report.attempted == 2 and report.not_found == 0
    assert [d["status"] for d in report.details] == ["geocode_error"] * 2
    assert report.retriable == 2  # counted (and the OBSERV summary is status=warn)


class _Scripted:
    """chat_completion answers from a list; records evictions."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.evicted = []

    def chat_completion(self, **_):
        return self.replies.pop(0)

    def evict_completion(self, prompt, system_prompt, cache_type, temperature):
        self.evicted.append(cache_type)
        return True


def test_an_unreadable_reply_is_evicted_and_asked_once_more() -> None:
    from src.enrichment.places_grok_geocode import ask_geocode

    good = '{"found": true, "latitude": 50.77, "longitude": 6.08, "confidence": 0.9}'
    client = _Scripted("I think it is near Aachen", good)
    result = ask_geocode(client, "p", "s", "grok")
    assert result.found and result.latitude == 50.77 and client.evicted == ["places"]


def test_a_reply_unreadable_twice_is_left_retriable_not_not_found(tmp_path) -> None:
    from src.enrichment.places_grok_geocode import ask_geocode

    client = _Scripted("no idea", '{"found": "maybe", "latitude": "north"}')
    result = ask_geocode(client, "p", "s", "grok")
    assert result.unparsed and not result.found and client.evicted == ["places"] * 2
    place: dict = {}
    from src.enrichment.places_grok_geocode import _non_geocoded_status

    assert _non_geocoded_status(place, result) == "geocode_error"  # non-terminal


def test_unreadable_replies_do_not_look_like_a_geocoder_outage(tmp_path, monkeypatch):
    import src.enrichment.places_grok_geocode as pg

    places = tmp_path / "places"
    places.mkdir()
    for i in range(pg.GEOCODER_DOWN_STREAK + 1):
        _place_file(
            places, f"p{i}.json", {"PlaceID": f"P{i}", "current_name": f"Town{i}"}
        )
    alerts = []
    monkeypatch.setattr(pg, "_alert_geocoder_down", alerts.append)
    client = _Scripted(*["no JSON"] * 2 * (pg.GEOCODER_DOWN_STREAK + 1))
    report = geocode_places_dir(places, client, write=False)
    assert alerts == [] and report.attempted == pg.GEOCODER_DOWN_STREAK + 1


def test_parse_reply_tolerates_prose_and_fences() -> None:
    from src.enrichment.places_grok_geocode import _parse_geocode_reply

    # Wrapped in prose + code fence (a reasoning model's habit).
    reply = (
        "Here are the coordinates:\n```json\n"
        '{"found": true, "latitude": 50.77, "longitude": 6.08, "confidence": 0.9}\n'
        "```\nHope that helps!"
    )
    result = _parse_geocode_reply(reply)
    assert result.found is True
    assert result.latitude == 50.77
    # A reply with no JSON is a not-found, not a crash.
    assert _parse_geocode_reply("I don't know that place.").found is False


def test_the_cascade_keeps_an_unreadable_grok_reply_retriable() -> None:
    """Phase 3's cascade (Nominatim -> hill -> Grok): a Nominatim miss then an unreadable Grok
    reply is geocode_error (retried), never a terminal geocode_not_found."""
    from src.enrichment.places_grok_geocode import (
        _apply_result,
        cascade_geocoder,
        geocode_place,
    )

    def nominatim_miss(name, place, client):
        return GeocodeResult(found=False, confidence=0.0, source="osm")

    geocode = cascade_geocoder(nominatim_miss, geocode_place)
    result = geocode("Hill 400", {}, _Scripted("sorry", "still sorry"))
    assert result.unparsed and _apply_result({}, result) == "geocode_error"


def test_grok_errors_do_not_look_like_a_nominatim_outage(tmp_path, monkeypatch):
    import src.enrichment.places_grok_geocode as pg

    places = tmp_path / "places"
    places.mkdir()
    for i in range(pg.GEOCODER_DOWN_STREAK + 1):
        _place_file(
            places, f"p{i}.json", {"PlaceID": f"P{i}", "current_name": f"Town{i}"}
        )
    alerts = []
    monkeypatch.setattr(pg, "_alert_geocoder_down", alerts.append)

    class _Boom:
        def chat_completion(self, **_):
            raise RuntimeError("bad request")

    geocode_places_dir(places, _Boom(), write=False)
    assert alerts == []


def test_a_batch_collect_is_not_a_geocode_error() -> None:
    from src.enrichment.places_grok_geocode import ask_geocode
    from src.grok_client import BatchModeCollecting

    class _Collecting:
        batch_mode = True

        def chat_completion(self, **_):
            raise BatchModeCollecting("k")

    with pytest.raises(BatchModeCollecting):
        ask_geocode(_Collecting(), "p", "s", "grok")


def test_a_reply_cannot_set_our_own_flags() -> None:
    from src.enrichment.places_grok_geocode import _parse_geocode_reply

    r = _parse_geocode_reply('{"found": false, "unparsed": true, "transient": true}')
    assert r.unparsed is False and r.transient is False  # an honest not-found
