"""Guard: place geo flags must read the canonical nested ``coordinates`` object.

XChannel ch8 e2e: 36/36 places carried populated ``coordinates.{latitude,longitude}`` yet all
were flagged geo_missing_coords -> a false "SYSTEMATIC ... likely a CODE defect" alarm.
"""

from src.utils.semantic_flags import flags_for


def test_nested_coords_are_not_missing():
    rec = {"name": "Carentan", "coordinates": {"latitude": 49.3, "longitude": -1.25}}
    assert "geo_missing_coords" not in flags_for("places", rec)


def test_nested_impossible_coords_flag():
    rec = {"name": "X", "coordinates": {"latitude": 999, "longitude": -1.25}}
    assert flags_for("places", rec) == ["geo_impossible_coords"]


def test_nested_zero_placeholder_is_missing():
    rec = {"name": "X", "coordinates": {"latitude": 0.0, "longitude": 0.0}}
    assert flags_for("places", rec) == ["geo_missing_coords"]


def test_no_coords_anywhere_is_missing():
    assert flags_for("places", {"name": "X"}) == ["geo_missing_coords"]
    assert flags_for("places", {"name": "X", "coordinates": None}) == [
        "geo_missing_coords"
    ]


def test_root_fallback_still_works():
    assert flags_for("places", {"latitude": 49.3, "longitude": -1.25}) == []
