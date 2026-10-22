"""Places extraction: null-coordinate LANDMARKS (hills + situational/relative features) must
SURVIVE Phase 2 so the Phase-3 geocoders (hill_geocode / situational + Grok fallback) can resolve
them from context. Previously _is_valid_place_mention dropped ANY coordinate-less place except a
route, so "Hill 555" / "east bank of the Loire" were lost before Phase 3 ever ran. A null-coord
SETTLEMENT is still dropped (a genuine extraction miss). Also checks the places.yaml guidance.
"""

# pylint: disable=protected-access

import re

import yaml

from src.extraction import places as pl


def _m(**kw):
    base = {"current_name": "X", "geography_type": "city"}
    base.update(kw)
    return base


def test_hill_with_null_coords_is_kept():
    # "Hill 401" is a named-height feature (hill_geocode.is_height_feature) -> keep for Phase 3.
    m = _m(
        current_name="Hill 401", geography_type="hill", latitude=None, longitude=None
    )
    assert pl._is_valid_place_mention(m) is True


def test_situational_relative_with_null_coords_is_kept():
    for gtype in ("situational", "relative_position", "ridge", "bridgehead"):
        m = _m(
            current_name="Aachen Gap",
            geography_type=gtype,
            latitude=None,
            longitude=None,
        )
        assert pl._is_valid_place_mention(m) is True, gtype


def test_east_bank_relative_kept():
    m = _m(
        current_name="east bank of the Loire",
        geography_type="relative_position",
        latitude=None,
        longitude=None,
    )
    assert pl._is_valid_place_mention(m) is True


def test_settlement_with_null_coords_is_dropped():
    # A city/region with no coordinate is a genuine extraction miss -> drop (unchanged behavior).
    m = _m(current_name="Aachen", geography_type="city", latitude=None, longitude=None)
    assert pl._is_valid_place_mention(m) is False
    m2 = _m(
        current_name="Rhineland",
        geography_type="region",
        latitude=None,
        longitude=None,
    )
    assert pl._is_valid_place_mention(m2) is False


def test_place_with_coords_is_kept():
    m = _m(current_name="Aachen", geography_type="city", latitude=50.77, longitude=6.08)
    assert pl._is_valid_place_mention(m) is True


def test_route_exemption_preserved():
    m = {
        "current_name": "Supply Route A",
        "route": [],
        "latitude": None,
        "longitude": None,
    }
    assert pl._is_valid_place_mention(m) is True


def test_null_current_name_dropped():
    assert pl._is_valid_place_mention({"current_name": None}) is False


def test_hill_kept_even_if_geography_type_mislabeled():
    # Detection falls back to the name pattern, so a mislabeled type still keeps a real hill.
    m = _m(
        current_name="Height 192",
        geography_type="Unknown",
        latitude=None,
        longitude=None,
    )
    assert pl._is_valid_place_mention(m) is True


# --- prompt guidance ---


def test_places_prompt_renders_and_has_landmark_guidance():
    from src.utils.prompt_loader import render_prompt, get_system_prompt

    p = render_prompt(
        "places",
        event_name="E",
        event_id="01E",
        sub_event_summary="S",
        sub_event_id="01S",
        text="Hill 401 near Vossenack.",
    )
    assert not re.findall(r"\{[a-z_]+\}", p), "unfilled placeholder in rendered prompt"
    assert get_system_prompt("places")
    # key new instructions present
    low = p.lower()
    assert "hill" in low and "null" in low  # hills -> null coords, Phase 3 geocodes
    assert "original_text" in low and "verbatim" in low  # verbatim source preservation
    assert (
        "never fabricate" in low or "not fabricate" in low or "do not fabricate" in low
    )


def test_places_prompt_schema_shows_null_coord_hill_example():
    d = yaml.safe_load(open("prompts/places.yaml"))
    import json

    schema = json.loads(d["schema"])
    hill = next(
        (m for m in schema["Place_Mentions"] if m.get("geography_type") == "hill"), None
    )
    assert hill is not None, "schema should show a hill example"
    assert hill["latitude"] is None and hill["longitude"] is None
    assert hill["original_text"]  # verbatim present
