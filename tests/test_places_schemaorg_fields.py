"""Places schema.org additive fields (sameAs, containedInPlace/containsPlace, geo GeoShape,
additionalProperty, elevation_m) — all nullable/additive; backward-compatible."""

import jsonschema
import pytest

from src.schemas.places_output import PLACES_OUTPUT_SCHEMA as S

ULID = "01HX7P5Q8ZB4K2N9VY3MCTE7RD"


def _valid(rec):
    jsonschema.validate(rec, S)


def _invalid(rec):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(rec, S)


def test_backward_compatible_old_record_still_valid():
    # a pre-2.30 places record (none of the new fields) must still validate
    _valid({"PlaceID": ULID, "current_name": "Saint-Lô"})


def test_full_record_with_all_new_fields():
    _valid(
        {
            "PlaceID": ULID,
            "current_name": "Omaha Beach",
            "sameAs": [
                "https://www.wikidata.org/wiki/Q1762179",
                "https://www.geonames.org/2981280/",
            ],
            "containedInPlace": [{"PlaceID": ULID, "name": "Normandy"}],
            "containsPlace": [{"PlaceID": ULID, "name": "Vierville sector"}],
            "geo": {
                "shape_type": "line",
                "vertices": [
                    {"lat": 49.3861, "long": -0.90},
                    {"lat": 49.3540, "long": -0.82},
                ],
                "source_date": "1944-06-06",
                "SourceSectionID": ULID,
                "confidence": 0.6,
            },
            "additionalProperty": [
                {"propertyID": "grid_reference", "value": "T 64/88"},
                {"propertyID": "hill_number", "value": 400, "unitCode": None},
            ],
            "elevation_m": 42.0,
        }
    )


def test_geo_polygon_and_circle():
    _valid(
        {
            "PlaceID": ULID,
            "geo": {
                "shape_type": "polygon",
                "vertices": [
                    {"lat": 49.39, "long": -0.95},
                    {"lat": 49.34, "long": -0.80},
                ],
            },
        }
    )
    _valid(
        {
            "PlaceID": ULID,
            "geo": {
                "shape_type": "circle",
                "vertices": [{"lat": 49.37, "long": -0.87}],
                "radius_m": 1500,
            },
        }
    )


def test_new_fields_nullable():
    _valid(
        {
            "PlaceID": ULID,
            "sameAs": None,
            "containedInPlace": None,
            "containsPlace": None,
            "geo": None,
            "additionalProperty": None,
            "elevation_m": None,
        }
    )


def test_elevation_is_numeric():
    _valid({"PlaceID": ULID, "elevation_m": -5})  # below sea level ok
    _invalid({"PlaceID": ULID, "elevation_m": "high"})  # string rejected


def test_geo_shape_type_enum_enforced():
    _invalid({"PlaceID": ULID, "geo": {"shape_type": "blob"}})


def test_geo_vertex_bounds_enforced():
    _invalid(
        {
            "PlaceID": ULID,
            "geo": {"shape_type": "point", "vertices": [{"lat": 999, "long": 0}]},
        }
    )  # lat out of range


def test_geo_additionalProperties_closed():
    _invalid({"PlaceID": ULID, "geo": {"shape_type": "point", "bogus": 1}})


def test_additionalProperty_requires_propertyID_value_shape():
    _invalid(
        {
            "PlaceID": ULID,
            "additionalProperty": [{"propertyID": "x", "value": 1, "extra": 2}],
        }
    )


def test_sameAs_is_url_array():
    _valid({"PlaceID": ULID, "sameAs": ["https://www.wikidata.org/wiki/Q1"]})
    # non-array sameAs rejected
    _invalid({"PlaceID": ULID, "sameAs": "https://x"})


def test_containedInPlace_closed_shape():
    _invalid({"PlaceID": ULID, "containedInPlace": [{"PlaceID": ULID, "nope": 1}]})
