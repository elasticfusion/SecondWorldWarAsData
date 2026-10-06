"""Strict output schema for people files (output/people/*.json)."""

from src.schemas import (
    EVENT_MENTIONS_SCHEMA,
    METADATA_PROPERTIES,
    date_field,
    entity_version,
    enum_field,
    make_nullable,
    ulid_field,
    url_field,
)

PEOPLE_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("people"),
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
                "birth_place": make_nullable("string"),
                "death_place": make_nullable("string"),
                "family": {"type": ["array", "object", "string", "null"]},
                "nationality": make_nullable("string"),
                "nationality_served": make_nullable("string"),
                "role_type": make_nullable("string"),
                "primary_group_id": make_nullable("string"),
                "biographical_details": make_nullable("string"),
                "ranks": {
                    "type": ["array", "null"],
                    "items": {
                        "type": ["object", "string"],
                        "properties": {
                            "rank": {"type": "string"},
                            "date": make_nullable("string"),
                            "branch": make_nullable("string"),
                        },
                    },
                },
                "military_awards": {
                    "type": ["array", "null"],
                    "items": {
                        "type": ["object", "string"],
                        "properties": {
                            "award": {"type": "string"},
                            "class": make_nullable("string"),
                            "date_awarded": make_nullable("string"),
                        },
                    },
                },
                "aliases": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "biography_sources": {
                    "type": ["array", "null"],
                    "items": {
                        "type": ["object", "string"],
                        "properties": {
                            "source": make_nullable("string"),
                            "page": make_nullable("string"),
                            "confidence": {"type": ["number", "string", "null"]},
                            "fields_sourced": {"type": ["array", "null"]},
                        },
                    },
                },
                "units_served": {
                    "type": ["array", "null"],
                    "items": {
                        "type": ["object", "string"],
                        "properties": {
                            "unit": {"type": "string"},
                            "from": make_nullable("string"),
                            "to": make_nullable("string"),
                            "GroupID": make_nullable("string"),
                        },
                    },
                },
                "group_affiliations": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "group": {"type": "string"},
                            "GroupID": make_nullable("string"),
                            "group_kind": make_nullable("string"),
                            "implied_from_title": {"type": ["boolean", "null"]},
                            "date_verified": {"type": ["boolean", "null"]},
                            "as_of_source_date": make_nullable("string"),
                            "source_title": make_nullable("string"),
                        },
                    },
                },
                "education": {
                    "type": ["array", "null"],
                    "items": {
                        "type": ["object", "string"],
                        "properties": {
                            "degree": make_nullable("string"),
                            "institution": make_nullable("string"),
                            "year": {"type": ["number", "string", "null"]},
                        },
                    },
                },
                "wikipedia_url": url_field(nullable=True),
                "grokipedia_url": url_field(nullable=True),
            },
        },
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "enrichment_status": enum_field(["enriched", "not_found"], nullable=True),
        "last_enrichment_search": date_field(nullable=True),
        "openserp_searched": {"type": ["boolean", "null"]},
        "openserp_searched_at": {"type": ["number", "string", "null"]},
        "oob_convergence_note": make_nullable("string"),
        "name_resolution": {"type": ["object", "null"]},
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
