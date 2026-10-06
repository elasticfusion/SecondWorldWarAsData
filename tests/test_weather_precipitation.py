"""Narrative vs scientific precipitation: both valid, non-redundant, on one record.
Narrative ('3 inches of snow') in extracted_data; scientific (3.2 in / 81mm) in
noaa_observed. Narrative amount only when the source states a number (else text only).
"""

import jsonschema
from src.extraction.weather_central import (
    _normalize_precip_unit,
    _normalize_precip_type,
)
from src.schemas.weather_output import WEATHER_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


def test_precip_unit_type_normalize():
    assert _normalize_precip_unit("inches") == "in"
    assert _normalize_precip_unit("cm") == "cm"
    assert _normalize_precip_unit("furlongs") is None
    assert _normalize_precip_type("snowfall") == "snow"
    assert _normalize_precip_type(None) is None


def test_narrative_and_scientific_coexist_non_redundant():
    # "3 inches of snow" (narrative) AND NOAA 81.3mm (3.2in observed) at St. Vith 15 Dec 1944
    rec = {
        "WeatherID": UL,
        "date": "1944-12-15",
        "location": {
            "place_name": "St. Vith",
            "PlaceID": UL,
            "latitude": 50.28,
            "longitude": 6.13,
        },
        "source_type": "hybrid",
        "extracted_data": {
            "description": "snow",
            "precipitation_text": "3 inches of snow",
            "precipitation_amount": 3,
            "precipitation_unit": "in",
            "precipitation_type": "snow",
            "original_text": "Three inches of snow fell on December 15",
        },
        "noaa_observed": {
            "snowfall_mm": 81.3,
            "station_id": "GHCND:BE000006447",
            "source": "noaa_cdo",
            "source_url": "https://x",
            "data_type": "observed",
        },
    }
    jsonschema.validate(rec, S)
    # both present, distinguishable, NOT merged
    assert rec["extracted_data"]["precipitation_amount"] == 3  # narrative (vague-ish)
    assert rec["noaa_observed"]["snowfall_mm"] == 81.3  # scientific (precise)


def test_vague_narrative_keeps_text_only():
    rec = {
        "WeatherID": UL,
        "date": "1944-12-15",
        "location": {"place_name": "x"},
        "source_type": "extracted",
        "extracted_data": {
            "precipitation_text": "heavy snow",
            "precipitation_amount": None,
            "precipitation_unit": None,
            "precipitation_type": "snow",
        },
    }
    jsonschema.validate(
        rec, S
    )  # amount null (no number stated) -> valid, not fabricated
