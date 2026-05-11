"""Strict output schema for places files (output/places/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

PLACES_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Places Output File",
    "type": "object",
    "required": ["PlaceID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "PlaceID": ulid_field(),
        "current_name": make_nullable("string"),
        "name": make_nullable("string"),
        "historical_names": {"type": ["array", "null"], "items": {"type": "string"}},
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "source_language": make_nullable("string"),
        "geography_type": make_nullable("string"),
        "coordinates": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
                "precision": enum_field(
                    ["exact", "approximate", "estimated"], nullable=True
                ),
                "confidence": {"type": ["number", "null"]},
            },
        },
        "bounding_box": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "north": {"type": "number"},
                "south": {"type": "number"},
                "east": {"type": "number"},
                "west": {"type": "number"},
            },
        },
        "map_urls": {
            "type": ["object", "null"],
            "properties": {
                "google_maps": make_nullable("string"),
                "openstreetmap": make_nullable("string"),
            },
        },
        "related_places": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "PlaceID": ulid_field(),
                    "relationship": make_nullable("string"),
                },
            },
        },
        "hierarchy": {"type": ["array", "object", "null"]},
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "MentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Event_Name": make_nullable("string"),
                    "Sub_eventID": ulid_field(nullable=True),
                    "Sub_event_Name": make_nullable("string"),
                    "book": make_nullable("string"),
                    "author": make_nullable("string"),
                    "series": make_nullable("string"),
                    "context": make_nullable("string"),
                    "original_text": make_nullable("string"),
                    "role_in_event": make_nullable("string"),
                    "date_context": make_nullable("string"),
                    "DateMentionID": ulid_field(nullable=True),
                    "nationality": make_nullable("string"),
                },
            },
        },
        "enrichment_status": enum_field(["enriched", "not_found"], nullable=True),
        "last_enrichment_search": {"type": ["string", "null"]},
    },
}
