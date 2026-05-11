"""Strict output schema for groups files (output/people_groups/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

GROUPS_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Groups Output File",
    "type": "object",
    "required": ["GroupID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "GroupID": ulid_field(),
        "group_name": make_nullable("string"),
        "common_name": make_nullable("string"),
        "name": make_nullable("string"),
        "group_type": make_nullable("string"),
        "source_language": make_nullable("string"),
        "nationality": make_nullable("string"),
        "country_of_origin": make_nullable("string"),
        "alliance_membership": {
            "type": ["array", "string", "null"],
            "items": {"type": "string"},
        },
        "description": make_nullable("string"),
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "parent_organization": make_nullable("string"),
        "sub_organizations": {"type": ["array", "null"], "items": {"type": "string"}},
        "military_hierarchy": {"type": ["object", "array", "string", "null"]},
        "enrichment_data": {"type": ["object", "null"]},
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
                },
            },
        },
        "enrichment_status": enum_field(["enriched", "not_found"], nullable=True),
        "last_enrichment_search": {"type": ["string", "null"]},
    },
}
