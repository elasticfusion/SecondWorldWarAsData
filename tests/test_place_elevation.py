"""T2-B: places.elevation_m from the DEM at resolved coords (opt-in, fail-safe, never from name)."""

import src.enrichment.places_grok_geocode as G
from src.enrichment.places_grok_geocode import (
    GeocodeResult,
    _apply_result,
    configure_elevation,
)


def _geocoded():
    return GeocodeResult(
        found=True,
        latitude=49.11,
        longitude=-1.09,
        country="France",
        confidence=0.9,
        source="osm",
    )


def teardown_function():
    configure_elevation(False, None)  # reset global


def test_elevation_off_by_default(tmp_path):
    configure_elevation(False, None)
    place = {"PlaceID": "x"}
    _apply_result(place, _geocoded())
    assert "elevation_m" not in place  # default OFF -> not populated


def test_elevation_captured_when_enabled(tmp_path, monkeypatch):
    configure_elevation(True, tmp_path / "elev")
    monkeypatch.setattr(
        "src.enrichment.elevation_verify.terrain_elevation",
        lambda lat, lon, cache_dir, session=None: 168.4,
    )
    place = {"PlaceID": "x"}
    _apply_result(place, _geocoded())
    assert (
        place["elevation_m"] == 168.4
    )  # the DEM value at the coords (NOT a hill-number)


def test_elevation_failsafe_on_lookup_none(tmp_path, monkeypatch):
    configure_elevation(True, tmp_path / "elev")
    monkeypatch.setattr(
        "src.enrichment.elevation_verify.terrain_elevation",
        lambda lat, lon, cache_dir, session=None: None,
    )
    place = {"PlaceID": "x"}
    _apply_result(place, _geocoded())
    assert "elevation_m" not in place  # null-over-guess: miss -> absent, no crash


def test_elevation_failsafe_on_lookup_error(tmp_path, monkeypatch):
    configure_elevation(True, tmp_path / "elev")

    def _boom(*a, **k):
        raise RuntimeError("DEM down")

    monkeypatch.setattr("src.enrichment.elevation_verify.terrain_elevation", _boom)
    place = {"PlaceID": "x"}
    _apply_result(place, _geocoded())  # must not raise
    assert "elevation_m" not in place
