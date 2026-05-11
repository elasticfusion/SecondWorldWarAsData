"""Strict output schema for bibliography files (output/bibliography/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

BIBLIOGRAPHY_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Bibliography Output File",
    "type": "object",
    "required": ["BibliographyID", "title"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "BibliographyID": ulid_field(),
        "title": {"type": "string"},
        "alt_title": make_nullable("string"),
        "citation": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "author": {"type": ["array", "null"], "items": {"type": "string"}},
                "title": make_nullable("string"),
                "alt_title": make_nullable("string"),
                "publisher": make_nullable("string"),
                "publication_date": make_nullable("string"),
                "first_edition_date": make_nullable("string"),
                "publication_location": make_nullable("string"),
                "publication_country": make_nullable("string"),
                "isbn": make_nullable("string"),
                "isbn_edition": make_nullable("string"),
                "pages": make_nullable("string"),
                "volume": make_nullable("string"),
                "edition": make_nullable("string"),
                "translator": make_nullable("string"),
                "periodical_name": make_nullable("string"),
                "alt_periodical_name": make_nullable("string"),
                "document_type": make_nullable("string"),
                "author_death_date": make_nullable("string"),
            },
        },
        "availability": enum_field(
            ["online", "offline", "archive", "unknown"], nullable=True
        ),
        "resource_urls": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "archive_reference_number": make_nullable("string"),
        "archive_physical_address": make_nullable("string"),
        "license": make_nullable("string"),
        "license_notes": make_nullable("string"),
        "copyright_status": {
            "type": ["object", "null"],
            "properties": {
                "status": make_nullable("string"),
                "author_death_date": make_nullable("string"),
                "determination_basis": make_nullable("string"),
                "jurisdiction": make_nullable("string"),
            },
        },
        "search_status": enum_field(
            ["resolved", "not_found", "pending"], nullable=True
        ),
        "search_source": make_nullable("string"),
        "download_status": enum_field(
            ["pending", "downloaded", "extracted", "gated", "skipped", "error"],
            nullable=True,
        ),
        "download_path": make_nullable("string"),
        "mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "MentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Sub-eventID": ulid_field(),
                    "book": make_nullable("string"),
                    "chapter": make_nullable("string"),
                    "reference_type": make_nullable("string"),
                    "reference_number": make_nullable("string"),
                    "verbatim_reference": make_nullable("string"),
                    "volume": make_nullable("string"),
                    "pages": make_nullable("string"),
                },
            },
        },
    },
}
