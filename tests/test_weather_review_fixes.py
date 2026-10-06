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


def test_date_link_exact_and_interval():
    from src.extraction.weather_central import _resolve_date_link

    lookup = {
        "1944-06-06": {
            "DateID": "01EXACT",
            "resolved_earliest": "1944-06-06T00:00:00",
            "resolved_latest": "1944-06-06T23:59:59",
            "time_source": "Allied",
        },
        "early-1944-06": {
            "DateID": "01APPROX",
            "resolved_earliest": "1944-06-01T00:00:00",
            "resolved_latest": "1944-06-10T23:59:59",
            "time_source": "German",
        },
    }
    # exact match wins + carries time_source
    assert _resolve_date_link("1944-06-06", lookup) == ("01EXACT", "Allied")
    # a weather day with no exact date record but inside the approximate interval links to it
    assert _resolve_date_link("1944-06-03", lookup) == ("01APPROX", "German")
    # outside any interval -> no link
    assert _resolve_date_link("1944-07-01", lookup) == (None, None)


def test_single_weather_prompt_file_live():
    """The live weather path uses weather_batch for BOTH template and system prompt;
    the dead weather.yaml is gone (no split-brain, no legacy temperature shape)."""
    from pathlib import Path
    from src.utils.prompt_loader import get_system_prompt, load_prompt

    assert not (
        Path("prompts/weather.yaml").exists()
    ), "dead prompts/weather.yaml should be removed"
    sysp = get_system_prompt("weather_batch")
    assert sysp and "weather" in sysp.lower()
    tmpl = load_prompt("weather_batch")
    assert "prompt_template" in tmpl and "schema" in tmpl
