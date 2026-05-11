"""Strict output schema for event files (output/content/{Book}/*-event.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    make_nullable,
    ulid_field,
)

EVENTS_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Events Output File",
    "type": "object",
    "required": ["Event"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "Chapter": {"type": ["object", "string", "null"]},
        "Event": {
            "type": "object",
            "required": ["EventID", "Sub-events"],
            "properties": {
                "EventID": ulid_field(),
                "Event_Name": make_nullable("string"),
                "Sub-events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["Sub-eventID"],
                        "properties": {
                            "Sub-eventID": ulid_field(),
                            "Sub-event_summary": make_nullable("string"),
                            "Sub-event_fulltext": {
                                "type": ["string", "object", "null"]
                            },
                            "Endnote_References": {"type": ["array", "null"]},
                            "Footnote_References": {"type": ["array", "null"]},
                            "dates": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                            },
                            "places": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                            },
                            "people": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                            },
                            "groups": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                            },
                            "equipment": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}
