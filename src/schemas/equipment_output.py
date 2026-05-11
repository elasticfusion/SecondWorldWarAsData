"""Strict output schema for equipment files (output/equipment/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

EQUIPMENT_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Equipment Output File",
    "type": "object",
    "required": ["EquipmentID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "EquipmentID": ulid_field(),
        "common_name": make_nullable("string"),
        "technical_identifier": make_nullable("string"),
        "category": make_nullable("string"),
        "subcategory": make_nullable("string"),
        "country_of_origin": make_nullable("string"),
        "description": make_nullable("string"),
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "alternate_names": {"type": ["array", "null"], "items": {"type": "string"}},
        "variants": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "specifications": {"type": ["object", "null"]},
        "media": {"type": ["array", "object", "null"]},
        "external_data": {"type": ["object", "null"]},
        "extracted_date": make_nullable("string"),
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "MentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Sub_eventID": ulid_field(nullable=True),
                    "book": make_nullable("string"),
                    "context": make_nullable("string"),
                },
            },
        },
        "enrichment_status": enum_field(["enriched", "not_found"], nullable=True),
        "openserp_searched": {"type": ["boolean", "null"]},
        "images": {"type": ["array", "null"], "items": {"type": "object"}},
    },
}
