"""Strict output schema for casualties files (output/casualties/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

CASUALTIES_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Casualties Output File",
    "type": "object",
    "required": ["CasualtyID", "type"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "CasualtyID": ulid_field(),
        "type": {"type": "string"},  # kia, wia, mia, pow, non_battle, casualties, etc.
        "description": make_nullable("string"),
        "count": {"type": ["object", "integer", "number", "null"]},
        "date": {"type": ["object", "string", "null"]},
        "side": enum_field(["allied", "axis", "neutral", "civilian"], nullable=True),
        "source": {"type": ["object", "string", "null"]},
        "event_context": {
            "type": ["object", "null"],
            "properties": {
                "EventID": ulid_field(),
                "Sub-eventID": ulid_field(nullable=True),
                "book": make_nullable("string"),
                "chapter": make_nullable("string"),
            },
        },
        "impacted_organizations": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "impacted_people": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "impacted_places": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
    },
}
