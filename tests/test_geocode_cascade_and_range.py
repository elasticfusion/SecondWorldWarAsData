"""Regression for Phase-3 review HIGHs H1 (impossible coordinates persisted) + H2 (transient
masking -> permanent loss). Both are data-integrity defects found by the independent review.
"""

import jsonschema
import pytest

from src.enrichment.places_grok_geocode import (
    GeocodeResult,
    MIN_GEOCODE_CONFIDENCE,
    _apply_result,
    cascade_geocoder,
    _STATUS_NOT_FOUND,
    _STATUS_GEOCODED,
    _STATUS_ERROR,
)
from src.schemas.places_output import PLACES_OUTPUT_SCHEMA as SCHEMA


# --- H1: out-of-range coordinates rejected, not persisted as geocoded ----------------------
@pytest.mark.parametrize(
    "lat,lon", [(999.0, 0.0), (0.0, -500.0), (91.0, 0.0), (0.0, 181.0), (-91.0, 0.0)]
)
def test_h1_out_of_range_rejected(lat, lon):
    place = {"name": "X"}
    status = _apply_result(
        place,
        GeocodeResult(
            found=True, latitude=lat, longitude=lon, confidence=0.9, source="grok"
        ),
    )
    assert status == _STATUS_NOT_FOUND
    assert "coordinates" not in place  # NOT persisted
    assert place.get("latitude") is None or "latitude" not in place


def test_h1_in_range_still_geocodes():
    place = {"name": "Aachen"}
    status = _apply_result(
        place,
        GeocodeResult(
            found=True, latitude=50.78, longitude=6.08, confidence=0.9, source="grok"
        ),
    )
    assert status == _STATUS_GEOCODED
    assert place["coordinates"]["latitude"] == 50.78


def test_h1_schema_rejects_out_of_range():
    v = jsonschema.Draft7Validator(SCHEMA)
    good = {
        "PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "latitude": 50.78,
        "longitude": 6.08,
    }
    assert not list(v.iter_errors(good))
    bad = {
        "PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "latitude": 999.0,
        "longitude": 6.08,
    }
    assert list(v.iter_errors(bad))  # schema now rejects


# --- H2: cascade prefers a transient over a genuine miss (retriable, not terminal) ---------
def _g(result):
    return lambda name, place, client: result


def test_h2_transient_preferred_over_genuine_miss():
    osm = _g(
        GeocodeResult(found=False, confidence=0.0, transient=True, source="nominatim")
    )
    grok = _g(
        GeocodeResult(found=False, confidence=0.4, transient=False, source="grok")
    )
    casc = cascade_geocoder(osm, grok)
    out = casc("X", {}, None)
    assert (
        out.transient is True
    )  # the outage is preserved -> _apply_result keeps it retriable
    place = {"name": "X"}
    assert (
        _apply_result(place, out) == _STATUS_ERROR
    )  # retriable, NOT terminal not_found


def test_h2_confident_hit_still_wins_over_transient():
    osm = _g(
        GeocodeResult(found=False, confidence=0.0, transient=True, source="nominatim")
    )
    grok = _g(
        GeocodeResult(
            found=True, latitude=50.78, longitude=6.08, confidence=0.95, source="grok"
        )
    )
    casc = cascade_geocoder(osm, grok)
    out = casc("X", {}, None)
    assert out.found is True and out.confidence == 0.95  # confident hit wins


def test_h2_genuine_miss_still_terminal_when_no_transient():
    a = _g(
        GeocodeResult(found=False, confidence=0.1, transient=False, source="nominatim")
    )
    b = _g(GeocodeResult(found=False, confidence=0.3, transient=False, source="grok"))
    casc = cascade_geocoder(a, b)
    out = casc("X", {}, None)
    assert out.transient is False  # genuine miss stays terminal (no false retry loop)
