"""Strict output schema for source_section files (output/source_section/*.json).

A ``source_section`` is the coarse-grained anchor ABOVE the granular Event/Sub-event layer:
one record per top-level source section (a book *chapter*, a journal *article*, an
after-action *report*, a KTB *entry* — hence the source-neutral name, not "chapter"). It
summarizes the whole section and is the natural home for section-level enrichment that would
be wrong to attach to a single granular sub-event:

  * a synthesized ``section_summary`` of the entire section;
  * a DERIVED canonical ``operation``/campaign label ("Battle of the Bulge") — null-over-fake
    (a pure-logistics section resolves to ``operation: null``, never a forced guess);
  * ``reference_articles`` — Grokipedia/Wikipedia article(s) for that operation kept as
    ADDITIVE reference material (extract/content) plus the article's OWN endnote references
    captured INLINE, raw and provenance-tagged. (Promotion of those references into the
    first-class ``bibliography`` entity is a BACKLOG item, not built here — see docs/TODO.md.)

Media (photos + maps) pulled from those articles are NOT stored inline: they are written as
first-class ``images`` / ``map_features`` records cross-linked back by ``SourceSectionID`` +
``EventID`` with full provenance, reusing the existing media handling.

Enrichment (operation derivation + Grok/Wiki fetch) runs in PHASE 2 with native commit, per
the by-source split (all Grokipedia/Wikipedia enrichment is Phase 2).
"""

from src.schemas import (
    METADATA_PROPERTIES,
    entity_version,
    make_nullable,
    ulid_field,
)

# A derived operation/campaign label. Null-over-fake: the whole object is null when the
# section cannot be confidently mapped to a known operation.
_OPERATION_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "properties": {
        "name": make_nullable("string"),
        "wikipedia_title": make_nullable("string"),
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "confidence": {"type": ["number", "null"]},
        "source": make_nullable("string"),  # provenance of the label, e.g. "derived"
    },
}

# One captured Grokipedia/Wikipedia article: additive text + the article's own references,
# raw and provenance-tagged. References are NOT promoted to bibliography here (backlog).
_REFERENCE_ARTICLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source": make_nullable("string"),  # "wikipedia" | "grokipedia"
        "title": make_nullable("string"),
        "url": make_nullable("string"),
        "extract": make_nullable("string"),  # additive article text/content
        "references": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": True,  # raw captured citation; shape not yet normalized
            },
        },
        "license": make_nullable("string"),
        "retrieved_at": make_nullable("string"),
    },
}

# Source provenance (book/author/series) carried from the originating document.
_SOURCE_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": True,
    "properties": {
        "book": make_nullable("string"),
        "author": make_nullable("string"),
        "series": make_nullable("string"),
    },
}

SOURCE_SECTION_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("source_section"),
    "title": "Source Section Output File",
    "type": "object",
    "required": ["SourceSectionID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "SourceSectionID": ulid_field(),
        "section_title": make_nullable("string"),
        "section_summary": make_nullable("string"),
        "source": _SOURCE_SCHEMA,
        # The per-section Event this record anchors (1 Event per section today).
        "EventID": make_nullable("string"),
        "operation": _OPERATION_SCHEMA,
        "reference_articles": {
            "type": ["array", "null"],
            "items": _REFERENCE_ARTICLE_SCHEMA,
        },
        # Gate marker for the Grok/Wiki fetch — stamped even on a miss so it is not re-pulled.
        "wikipedia_checked_at": make_nullable("string"),
        # Gate marker for the Wikipedia media fetch (idempotency — no duplicate image records).
        "media_checked_at": make_nullable("string"),
        # OpenSERP primary-source web results for the operation (Phase 3), keyed on the
        # operation label — the coarse anchor — not granular event names.
        "primary_sources": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": True,
            },
        },
        "openserp_searched": {"type": ["boolean", "null"]},
        "openserp_searched_at": {"type": ["number", "null"]},
    },
}
