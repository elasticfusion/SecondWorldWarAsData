"""Strict output schema for weather files (output/weather/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    ULID_PATTERN,
    date_field,
    enum_field,
    make_nullable,
    ulid_field,
)

WEATHER_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Weather Output File",
    "type": "object",
    "required": ["WeatherID", "date", "location", "source_type"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "WeatherID": ulid_field(),
        "date": {"type": "string"},  # Allows YYYY-MM-DD, YYYY-MM, and date ranges
        "DateID": ulid_field(nullable=True),
        "location": {
            "type": "object",
            "required": ["place_name", "latitude", "longitude"],
            "additionalProperties": False,
            "properties": {
                "place_name": {"type": "string"},
                "PlaceID": {"type": ["string", "null"]},
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
            },
        },
        "source_type": enum_field(["extracted", "api_only", "hybrid"]),
        "extracted_data": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "description": make_nullable("string"),
                "temperature": make_nullable("number"),
                "temperature_unit": {
                    "type": ["string", "null"],
                    "enum": ["C", "F", None],
                },
                "measurement_system": make_nullable("string"),
                "notable_impact": make_nullable("string"),
                "original_text": make_nullable("string"),
                "book": make_nullable("string"),
                "author": make_nullable("string"),
            },
        },
        "api_data": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "provider": enum_field(["open-meteo"]),
                "data_type": enum_field(["reanalysis"]),
                "retrieved_at": {"type": "string"},
                "temperature_max_c": make_nullable("number"),
                "temperature_min_c": make_nullable("number"),
                "precipitation_mm": make_nullable("number"),
                "windspeed_max_kmh": make_nullable("number"),
                "cloud_cover_percent": make_nullable("integer"),
                "raw_response": make_nullable("object"),
            },
        },
        "noaa_observed": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "temperature_high_c": make_nullable("number"),
                "temperature_low_c": make_nullable("number"),
                "precipitation_mm": make_nullable("number"),
                "wind_speed_ms": make_nullable("number"),
                "snowfall_mm": make_nullable("number"),
                "station_id": {"type": "string"},
                "station_distance_km": make_nullable("number"),
                "source": enum_field(["noaa_cdo"]),
                "source_url": {"type": "string"},
                "data_type": enum_field(["observed"]),
            },
        },
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "EventID": ulid_field(),
                    "Sub-eventID": ulid_field(),
                },
            },
        },
        "enrichment_status": enum_field(["enriched", "not_found"], nullable=True),
        "last_enrichment_search": date_field(nullable=True),
    },
}
