"""Enforced map_features schema: GeoJSON FeatureCollection with entity-graph links."""

import json
from pathlib import Path

import jsonschema
import pytest

from src.schemas.map_features_output import MAP_FEATURES_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


def _fc(**over):
    base = {
        "type": "FeatureCollection",
        "MapID": None,
        "map_number": "MAP III",
        "map_title": "The Ardennes",
        "legend": [
            {
                "symbol_description": "red arrow",
                "meaning": "German attack",
                "date_text": "16-19 DEC",
                "DateID": None,
                "confidence": 0.8,
            }
        ],
        "elevation_scale": "ELEVATIONS IN METERS 0 400 500 600",
        "distance_scale": "0 1 2 3 MILES",
        "features": [
            {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "feature_kind": "unit_position",
                    "source": "map",
                    "original_label": "18 VG",
                    "affiliation": "hostile",
                    "echelon": "division",
                    "branch": "infantry",
                    "GroupID": UL,
                    "PlaceID": UL,
                    "DateID": None,
                    "coordinate_source": "none",
                    "tile_id": "r0c1",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [6.33, 50.25]},
                "properties": {
                    "feature_kind": "place",
                    "source": "map",
                    "original_label": "Roth",
                    "PlaceID": UL,
                    "coordinate_source": "placeid_gazetteer",
                },
            },
        ],
    }
    base.update(over)
    return base


def test_valid_feature_collection():
    jsonschema.validate(_fc(), S)


def test_place_point_geometry_valid():
    jsonschema.validate(
        _fc(
            features=[
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [6.3, 50.2]},
                    "properties": {"feature_kind": "place", "source": "map"},
                }
            ]
        ),
        S,
    )


def test_coordinate_source_never_map_pixels():
    bad = _fc()
    bad["features"][0]["properties"]["coordinate_source"] = "map_pixels"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S)


def test_additional_properties_rejected():
    bad = _fc()
    bad["features"][0]["properties"]["bogus"] = "x"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S)


def test_bad_groupid_rejected():
    bad = _fc()
    bad["features"][0]["properties"]["GroupID"] = "not-a-ulid"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S)


def test_feature_kind_enum_enforced():
    bad = _fc()
    bad["features"][0]["properties"]["feature_kind"] = "spaceship"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S)


def test_real_prototype_output_validates_if_present():
    """If the prototype has produced output, it must validate against the schema."""
    p = Path("output/map_proto/map_III.features.json")
    if not p.exists():
        pytest.skip("prototype output not present")
    jsonschema.validate(json.loads(p.read_text()), S)
