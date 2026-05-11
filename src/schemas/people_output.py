"""Strict output schema for people files (output/people/*.json)."""

from src.schemas import (
    EVENT_MENTIONS_SCHEMA,
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    date_field,
    enum_field,
    make_nullable,
    ulid_field,
    url_field,
)

PEOPLE_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "People Output File",
    "type": "object",
    "required": ["PersonID", "name"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "PersonID": ulid_field(),
        "name": {"type": "string", "minLength": 1},
        "source_language": make_nullable("string"),
        "rank": make_nullable("string"),
        "nationality": make_nullable("string"),
        "side": enum_field(["allied", "axis", "neutral", "civilian"], nullable=True),
        "event_mentions": EVENT_MENTIONS_SCHEMA,
        "biographical_profile": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "birth_date": make_nullable("string"),
                "death_date": make_nullable("string"),
                "nationality": make_nullable("string"),
                "role_type": make_nullable("string"),
                "biographical_details": make_nullable("string"),
                "ranks": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "military_awards": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "aliases": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "biography_sources": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "units_served": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "education": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "wikipedia_url": url_field(nullable=True),
                "grokipedia_url": url_field(nullable=True),
            },
        },
        "enrichment_status": enum_field(["enriched", "not_found"], nullable=True),
        "last_enrichment_search": date_field(nullable=True),
        "openserp_searched": {"type": ["boolean", "null"]},
        "images": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": make_nullable("string"),
                    "source": make_nullable("string"),
                },
            },
        },
        "academic_references": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": make_nullable("string"),
                    "type": make_nullable("string"),
                    "source": make_nullable("string"),
                },
            },
        },
        "military_awards": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": make_nullable("string"),
                    "source": make_nullable("string"),
                },
            },
        },
    },
}
