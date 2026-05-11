"""Strict output schema for logistics files (output/logistics/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

LOGISTICS_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Logistics Output File",
    "type": "object",
    "required": ["LogisticsID", "logistics_type"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "LogisticsID": ulid_field(),
        "logistics_type": {"type": "string"},
        "category": make_nullable("string"),
        "description": make_nullable("string"),
        "severity": enum_field(["critical", "high", "medium", "low"], nullable=True),
        "status": make_nullable("string"),
        "temporal": {"type": ["object", "string", "null"]},
        "delivery_method": make_nullable("string"),
        "quantity": {"type": ["object", "string", "number", "null"]},
        "resolution": {"type": ["object", "string", "null"]},
        "extracted_date": make_nullable("string"),
        "impacted_organizations": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "impacted_people": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "impacted_equipment": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "MentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Sub_eventID": ulid_field(nullable=True),
                    "book": make_nullable("string"),
                    "chapter": make_nullable("string"),
                },
            },
        },
    },
}
