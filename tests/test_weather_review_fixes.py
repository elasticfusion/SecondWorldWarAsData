"""Weather review fixes: temperature_unit normalizes to the schema enum (C/F) so produced
files validate; NOAA enrichment reads the real 'date' field."""

import jsonschema
from src.extraction.weather_central import _normalize_temp_unit
from src.schemas.weather_output import WEATHER_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


def test_temp_unit_normalized_to_enum():
    assert _normalize_temp_unit("celsius") == "C"
    assert _normalize_temp_unit("Celsius") == "C"
    assert _normalize_temp_unit("fahrenheit") == "F"
    assert _normalize_temp_unit("C") == "C"
    assert _normalize_temp_unit(None) is None
    assert _normalize_temp_unit("kelvin") is None


def test_weather_record_with_temp_validates():
    # a produced-shape record (previously 'celsius' -> schema reject; now 'C')
    rec = {
        "WeatherID": UL,
        "date": "1944-12-22",
        "location": {
            "place_name": "Bastogne",
            "PlaceID": UL,
            "latitude": 50.0,
            "longitude": 5.7,
        },
        "source_type": "extracted",
        "extracted_data": {
            "description": "snow",
            "temperature": -5,
            "temperature_unit": _normalize_temp_unit("celsius"),
            "measurement_system": "metric",
        },
    }
    jsonschema.validate(rec, S)  # must not raise


def test_long_form_temp_unit_would_fail_schema():
    bad = {
        "WeatherID": UL,
        "date": "1944-12-22",
        "location": {"place_name": "x", "latitude": 1.0, "longitude": 1.0},
        "source_type": "extracted",
        "extracted_data": {"temperature_unit": "celsius"},
    }
    import pytest

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, S)  # proves the enum is enforced + why we normalize
