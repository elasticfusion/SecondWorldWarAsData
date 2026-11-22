"""Strict output schema for bibliography files (output/bibliography/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    QUALITY_FLAGS_PROPERTY,
    entity_version,
    enum_field,
    make_nullable,
    ulid_field,
)

# The ONLY form a bookseller listing may take (published on the site; the resolver builds it from
# the ASIN alone). (?![\s\S]) rather than $: Python's $ also matches before a trailing newline.
_BOOKSELLER_URL = r"^https://www\.amazon\.com/dp/[A-Z0-9]{10}(?![\s\S])"

BIBLIOGRAPHY_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("bibliography"),
    "title": "Bibliography Output File",
    "type": "object",
    "required": ["BibliographyID", "title"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        **QUALITY_FLAGS_PROPERTY,
        "BibliographyID": ulid_field(),
        # 2.31: the BibliographyIDs of records combined into this one (they cited the same
        # footnote text); a reference to one of them resolves to this record
        "merged_from": {
            "type": ["array", "null"],
            "items": ulid_field(),
            "uniqueItems": True,
        },
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
            ["online", "offline", "archive", "local", "unknown"], nullable=True
        ),
        # Provenance bag written by the resolver: prov keys from the biblio-note resolver + a
        # structured findings list. additionalProperties:true because the prov keys vary by source
        # type; this is internal provenance metadata. The keys that are PUBLISHED or relied on are
        # modeled: a bookseller listing (a pointer to buy, never an online copy; the URL is the
        # canonical https://www.amazon.com/dp/<ASIN> only, so a malformed or foreign URL cannot
        # reach the site) and when the listing search last ran.
        "source_metadata": {
            "type": ["object", "null"],
            "additionalProperties": True,
            "properties": {
                "findings": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "url": make_nullable("string"),
                            "source": make_nullable("string"),
                            "kind": enum_field(["bookseller"], nullable=True),
                        },
                        # a bookseller finding is held to the same URL rule as bookseller[]
                        "if": {
                            "properties": {"kind": {"const": "bookseller"}},
                            "required": ["kind"],
                        },
                        "then": {
                            "properties": {
                                "url": {"type": "string", "pattern": _BOOKSELLER_URL},
                                "source": {"enum": ["amazon"]},
                            },
                            "required": ["url", "source"],
                        },
                    },
                },
                "bookseller": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["url", "source", "kind"],
                        "properties": {
                            "url": {
                                "type": "string",
                                "pattern": _BOOKSELLER_URL,
                            },
                            "source": {"type": "string", "enum": ["amazon"]},
                            "kind": {"type": "string", "enum": ["bookseller"]},
                        },
                    },
                },
                "bookseller_searched_at": make_nullable("integer"),
            },
        },
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
        "source_qualification": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "description": (
                "QUALIFICATION (how a source qualifies for an asserted fact), NOT "
                "veracity. Two orthogonal axes + derivation provenance. Rating ladder: "
                "NARA/archival -> primary/contemporaneous = HIGH; densely-footnoted "
                "secondary -> secondary/dense = MED-HIGH; book w/ bibliography, few "
                "footnotes -> secondary/light = MED; bare web citing nothing -> "
                "secondary|tertiary/none = LOW; Grok fact with no cited URL -> unknown "
                "(flagged, untrusted) = LOWEST. See SOURCE_QUALIFICATION_DESIGN.md + "
                "URL_SOURCED_ENRICHMENT_DESIGN.md."
            ),
            "properties": {
                # Axis 1 — relationship to the event.
                "source_type": enum_field(
                    ["primary", "secondary", "tertiary", "reference", "unknown"],
                    nullable=True,
                ),
                # Axis 2a — proximity (meaningful for primary).
                "proximity": enum_field(
                    [
                        "participant",
                        "witness",
                        "contemporaneous",
                        "recollection",
                        "n/a",
                    ],
                    nullable=True,
                ),
                # Axis 2b — evidentiary support / footnote-apparatus (secondary/tertiary).
                "evidentiary_support": enum_field(
                    ["dense", "light", "none", "n/a"], nullable=True
                ),
                # Derivation provenance — so the qualifier signal is itself trustworthy.
                "method": enum_field(
                    ["derived", "llm_assisted", "manual"], nullable=True
                ),
                "basis": make_nullable("string"),
                "confidence": enum_field(["high", "medium", "low"], nullable=True),
            },
        },
        "search_status": enum_field(
            [
                "resolved",
                "not_found",
                "pending",
                "unresolved",
                # Statuses the resolver legitimately produces (were missing -> blocked at write):
                "held_unprocessed",  # raw source on hand, not yet ingested
                "not_citation",  # the "citation" was prose, not a real source reference
                "reference_identified",  # archival ref resolved (e.g. NARA RG) but no online URL yet
            ],
            nullable=True,
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
                    "source_url": make_nullable("string"),
                    "volume": make_nullable("string"),
                    "pages": make_nullable("string"),
                },
            },
        },
    },
}
