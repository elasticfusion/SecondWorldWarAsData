"""Guard: place geo flags must read the canonical nested ``coordinates`` object.

XChannel ch8 e2e: 36/36 places carried populated ``coordinates.{latitude,longitude}`` yet all
were flagged geo_missing_coords -> a false "SYSTEMATIC ... likely a CODE defect" alarm.
"""

import math

from src.dedup.resolved_set import _geo_cell
from src.utils.semantic_flags import flags_for, place_latlon


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


def test_partial_or_placeholder_nested_does_not_mask_complete_root():
    root = {"latitude": 49.3, "longitude": -1.25}
    for nested in (
        {"latitude": 49.3, "longitude": None},
        {"latitude": 0.0, "longitude": 0.0},
    ):
        assert flags_for("places", {"coordinates": nested, **root}) == []


def test_junk_values_are_missing_not_valid():
    for lat, lon in ((True, True), (math.nan, 1.0), (math.inf, 1.0), ("abc", 1.0)):
        rec = {"coordinates": {"latitude": lat, "longitude": lon}}
        assert flags_for("places", rec) == ["geo_missing_coords"], (lat, lon)


def test_numeric_strings_accepted():
    assert place_latlon({"coordinates": {"latitude": "49.3", "longitude": "-1.2"}}) == (
        49.3,
        -1.2,
    )


def test_dedup_geo_cell_reads_nested():
    assert (
        _geo_cell({"coordinates": {"latitude": 49.31, "longitude": -1.25}})
        == "49.3,-1.2"
    )
    assert _geo_cell({"name": "x"}) is None
