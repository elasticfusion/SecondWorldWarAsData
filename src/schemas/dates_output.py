"""Strict output schema for dates files (output/dates/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    entity_version,
    enum_field,
    make_nullable,
    ulid_field,
)

DATES_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("dates"),
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
        "date_precision": make_nullable("string"),
        "time_source": make_nullable("string"),
        "original_text": make_nullable("string"),
        "normalized_datetime": make_nullable("string"),
        # Deterministic resolution of the SOURCE-stated date into a sortable ISO interval
        # (never guessed; vague source -> wide interval). date_start/original_text remain
        # the verbatim authority; these are the derived, queryable bounds.
        "resolved_earliest": make_nullable(
            "string"
        ),  # ISO-8601 datetime (YYYY-MM-DDThh:mm:ssZ)
        "resolved_latest": make_nullable(
            "string"
        ),  # ISO-8601 datetime (YYYY-MM-DDThh:mm:ssZ)
        "resolution_method": enum_field(
            ["precision_rule", "range", "unresolved"], nullable=True
        ),
        # DERIVED, synthesized significance summary (what this date is about), generated
        # STRICTLY from this date's own event_mentions — a convenience layer, not an
        # authoritative fact (the mentions remain the source of truth). mention_count is a
        # cheap always-present importance signal.
        "summary": make_nullable("string"),
        "summary_source": enum_field(["synthesized"], nullable=True),
        "summary_generated_at": make_nullable("string"),
        "summary_mention_count": {"type": ["integer", "null"]},
        "summary_mentions_hash": make_nullable("string"),
        "mention_count": {"type": ["integer", "null"]},
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "DateMentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Sub_eventID": ulid_field(nullable=True),
                    "book": make_nullable("string"),
                    "chapter": make_nullable("string"),
                    "time_start": make_nullable("string"),
                    "original_text": make_nullable("string"),
                },
            },
        },
    },
}
