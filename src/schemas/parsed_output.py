"""Phase-1 parsed-content schema (HIGH-4b).

Validates the backbone of the ``chapter*-parsed.json`` records written by ``phase1_parse.py``
(``_doc_to_dict``). This is a CONTENT schema, not an entity schema: it is NOT in ENTITY_REGISTRY
(parse artifacts have no PK ULID and no cross-references, so none of the entity/PK/referential/dedup
machinery applies). It exists only to catch a GARBAGE or EMPTY parse at the earliest pipeline point
before it flows into Phase 2. ``additionalProperties`` is permissive on purpose — we check the
backbone (book + chapter + paragraphs), not every optional field.
"""

PARSED_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Phase-1 Parsed Chapter",
    "type": "object",
    "required": ["book", "chapter_title", "paragraphs"],
    "additionalProperties": True,
    "properties": {
        "book": {"type": "string"},
        "chapter_title": {"type": "string"},
        "chapter_number": {"type": ["integer", "string", "null"]},
        "section_id": {"type": ["string", "null"]},
        "author": {"type": ["string", "null"]},
        "series": {"type": ["string", "null"]},
        "license": {"type": ["string", "null"]},
        "source_file": {"type": ["string", "null"]},
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["absolute_number", "text"],
                "properties": {
                    "absolute_number": {"type": "integer"},
                    "text": {"type": "string"},
                    "page_number": {"type": ["integer", "string", "null"]},
                    "section_id": {"type": ["string", "null"]},
                },
            },
        },
        "images": {"type": ["array", "null"]},
        "maps": {"type": ["array", "null"]},
        "footnotes": {"type": ["array", "null"]},
        "table_hints": {"type": ["array", "object", "null"]},
    },
}
