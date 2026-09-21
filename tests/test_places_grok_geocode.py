"""Tests for Grok-backed place geocoding (write-back, confidence gating, idempotency).

Uses a fake Grok client — no network calls. Verifies that a confident hit writes
nested coordinates + derived fields, that low-confidence/not-found results are
recorded without guessing coordinates, and that runs are idempotent and bounded.
"""

import json
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
    _place_file(places, "aachen.json", {"PlaceID": "01A", "current_name": "Aachen"})
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
    _place_file(places, "hill.json", {"PlaceID": "01B", "current_name": "Hill 401"})
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
    _place_file(places, "x.json", {"PlaceID": "01C", "current_name": "Monunirel"})
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
        {"PlaceID": "01D", "current_name": "5th Division sector"},
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
            "PlaceID": "01E",
            "current_name": "Metz",
            "coordinates": {"latitude": 49.12, "longitude": 6.18},
        },
    )
    grok = _FakeGrok({})
    report = geocode_places_dir(places, grok, write=True)
    assert report.attempted == 0
    assert report.skipped == 1


def test_idempotent_after_attempt(tmp_path: Path) -> None:
    places = tmp_path / "places"
    places.mkdir()
    _place_file(places, "x.json", {"PlaceID": "01F", "current_name": "Monunirel"})
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
    _place_file(places, "a.json", {"PlaceID": "01", "current_name": "Aachen"})
    _place_file(places, "b.json", {"PlaceID": "02", "current_name": "Metz Town"})

    class _Boom:
        def chat_completion(self, **_):
            raise RuntimeError("api down")

    report = geocode_places_dir(places, _Boom(), write=False)
    assert report.errors == 2  # both attempted, both errored, no crash


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
