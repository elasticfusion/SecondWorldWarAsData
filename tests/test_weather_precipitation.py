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


def test_noaa_full_ghcnd_element_set():
    """NOAA layer captures the full standard GHCND daily set (unbiased baseline),
    and such a record validates even with NO narrative weather mention."""
    from src.enrichment.noaa_weather import DATATYPE_MAP

    # real GHCND codes only, full standard daily set
    assert set(DATATYPE_MAP) == {
        "TMAX",
        "TMIN",
        "TAVG",
        "PRCP",
        "SNOW",
        "SNWD",
        "AWND",
        "WSF2",
        "WSF5",
        "WDF2",
        "WDF5",
    }
    rec = {
        "WeatherID": UL,
        "date": "1944-06-22",
        "location": {"place_name": "Carentan"},
        "source_type": "api_only",  # no narrative mention — the quiet/benign-day baseline
        "noaa_observed": {
            "temperature_high_c": 22.2,
            "temperature_low_c": 12.1,
            "temperature_avg_c": 17.0,
            "precipitation_mm": 0.0,
            "snowfall_mm": 0.0,
            "snow_depth_mm": 0.0,
            "wind_speed_ms": 3.1,
            "wind_gust_fastest2min_ms": 7.2,
            "wind_gust_fastest5sec_ms": 9.4,
            "wind_dir_fastest2min_deg": 230,
            "wind_dir_fastest5sec_deg": 240,
            "station_id": "GHCND:FR000007139",
            "source": "noaa_cdo",
            "source_url": "https://x",
            "data_type": "observed",
        },
    }
    jsonschema.validate(rec, S)


def test_noaa_absorbs_all_elements_nothing_dropped(monkeypatch):
    """ALL NOAA elements are absorbed: named ones -> canonical fields, EVERY element
    (incl. unmapped WT01/WESD) preserved in raw_elements. Nothing dropped."""
    import src.enrichment.noaa_weather as nw

    fake = {
        "results": [
            {"datatype": "TMAX", "value": 22.2},  # mapped -> named field
            {"datatype": "WT01", "value": 1},  # unmapped weather-type flag (fog)
            {"datatype": "WESD", "value": 5.0},  # unmapped water-equiv snow depth
        ]
    }
    monkeypatch.setattr(nw, "_get", lambda *a, **k: fake)
    monkeypatch.setattr(nw, "get_cached", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(nw, "cache_result", lambda *a, **k: None, raising=False)
    # patch the lazily-imported cache fns used inside fetch
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *a, **k: None)

    obs = nw.fetch_noaa_weather("GHCND:X", "1944-12-15", "tok")
    assert obs["temperature_high_c"] == 22.2  # named promotion
    assert obs["raw_elements"]["WT01"] == 1  # unmapped preserved
    assert obs["raw_elements"]["WESD"] == 5.0  # unmapped preserved
    assert obs["raw_elements"]["TMAX"] == 22.2  # mapped also kept raw
    jsonschema.validate(
        {
            "WeatherID": UL,
            "date": "1944-12-15",
            "location": {"place_name": "x"},
            "source_type": "api_only",
            "noaa_observed": obs,
        },
        S,
    )
