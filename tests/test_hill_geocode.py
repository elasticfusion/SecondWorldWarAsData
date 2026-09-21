"""Tests for elevation-and-context-aware named-height geocoding (no network)."""

import json
from pathlib import Path

from src.enrichment.elevation_verify import check_designation
from src.enrichment.hill_geocode import (
    elevation_of,
    is_height_feature,
    make_hill_geocoder,
)
from src.enrichment.places_grok_geocode import GeocodeResult

# --- name parsing --------------------------------------------------------


def test_elevation_extracted_from_names() -> None:
    assert elevation_of("Hill 401") == 401
    assert elevation_of("Côte 192") == 192
    assert elevation_of("Height 314") == 314
    assert elevation_of("Hill 401.3") == 401  # decimal ignored
    assert elevation_of("Aachen") is None
    assert is_height_feature("Hill 90") is True
    assert is_height_feature("Aa River") is False


# --- elevation verification ---------------------------------------------


class _FakeElevResp:
    def __init__(self, elevation):
        self._e = elevation

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": [{"elevation": self._e}]}


class _FakeElevSession:
    def __init__(self, elevation):
        self._e = elevation

    def post(self, url, json=None, timeout=None):
        return _FakeElevResp(self._e)


def test_check_meters_match(tmp_path: Path) -> None:
    # Mortain Hill 314 sits on ~318 m terrain -> matches in meters.
    chk = check_designation(314, 48.65, -0.93, tmp_path, session=_FakeElevSession(318))
    assert chk.fits_meters is True
    assert chk.verified is True
    assert "meters" in chk.note


def test_check_gross_mismatch_flagged(tmp_path: Path) -> None:
    # "Hill 401" geocoded to an 800 m spot -> matches neither 401 m nor
    # 401 ft (=122 m); a genuine wrong-location signal.
    chk = check_designation(401, 43.5, -71.7, tmp_path, session=_FakeElevSession(800))
    assert chk.fits_meters is False
    assert chk.fits_feet is False
    assert "does not match" in chk.note


def test_check_unavailable_when_api_fails(tmp_path: Path) -> None:
    class _Boom:
        def post(self, **_):
            raise RuntimeError("down")

    chk = check_designation(300, 49.0, 6.0, tmp_path, session=_Boom())
    assert chk.terrain_m is None
    assert chk.verified is False


# --- end-to-end hill geocoder -------------------------------------------


class _FakeGrok:
    def __init__(self, reply: GeocodeResult):
        self._reply = reply

    def chat_completion(
        self, prompt, system_prompt=None, temperature=0.1, cache_type=None
    ):
        return self._reply.model_dump_json()


def _place(name):
    return {
        "current_name": name,
        "event_mentions": [
            {
                "Sub_event_Name": "objectives near St. Germain-d'Elle: Hills 90, 97",
                "original_text": name,
            }
        ],
    }


def test_hill_geocoder_verified_hit(tmp_path: Path) -> None:
    grok = _FakeGrok(
        GeocodeResult(
            found=True, latitude=49.13, longitude=-1.0, country="France", confidence=0.6
        )
    )
    geocode = make_hill_geocoder(tmp_path / "elev", session=_FakeElevSession(190))
    r = geocode("Hill 192", _place("Hill 192"), grok)
    assert r.found is True
    assert r.confidence >= 0.75  # verification boosted it
    assert "matches designation" in r.note
    assert r.source == "grok_hill"


def test_hill_geocoder_rejects_wrong_location(tmp_path: Path) -> None:
    # Grok returns a plausible-looking point, but terrain there is 800 m for a
    # 401 m designation (and not 401 ft either) -> rejected, not written.
    grok = _FakeGrok(
        GeocodeResult(found=True, latitude=43.5, longitude=-71.7, confidence=0.7)
    )
    geocode = make_hill_geocoder(tmp_path / "elev", session=_FakeElevSession(800))
    r = geocode("Hill 401", _place("Hill 401"), grok)
    assert r.found is False
    assert "rejected" in r.note


def test_hill_geocoder_unverified_capped(tmp_path: Path) -> None:
    class _Boom:
        def post(self, **_):
            raise RuntimeError("down")

    grok = _FakeGrok(
        GeocodeResult(found=True, latitude=49.1, longitude=-1.0, confidence=0.9)
    )
    geocode = make_hill_geocoder(tmp_path / "elev", session=_Boom())
    r = geocode("Hill 150", _place("Hill 150"), grok)
    assert r.found is True
    assert r.confidence <= 0.5  # unverified -> capped


def test_hill_geocoder_non_height_returns_miss(tmp_path: Path) -> None:
    grok = _FakeGrok(GeocodeResult(found=True, latitude=1, longitude=2, confidence=0.9))
    geocode = make_hill_geocoder(tmp_path / "elev", session=_FakeElevSession(300))
    r = geocode("Aachen", _place("Aachen"), grok)
    assert r.found is False
    assert "not a height feature" in r.note
