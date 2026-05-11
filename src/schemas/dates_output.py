"""Strict output schema for dates files (output/dates/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

DATES_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Dates Output File",
    "type": "object",
    "required": ["DateID", "date_start"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "DateID": ulid_field(),
        "date_start": {"type": "string"},
        "date_end": make_nullable("string"),
        "time_start": make_nullable("string"),
        "time_end": make_nullable("string"),
        "time_precision": make_nullable("string"),
        "date_precision": enum_field(
            [
                "exact",
                "early",
                "mid",
                "late",
                "seasonal",
                "approximate",
                "spring",
                "summer",
                "fall",
                "winter",
            ],
            nullable=True,
        ),
        "time_source": make_nullable("string"),
        "original_text": make_nullable("string"),
        "normalized_datetime": make_nullable("string"),
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
