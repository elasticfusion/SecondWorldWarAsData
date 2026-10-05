"""Strict output schema for equipment files (output/equipment/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

EQUIPMENT_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": SCHEMA_VERSION,
    "title": "Equipment Output File",
    "type": "object",
    "required": ["EquipmentID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "EquipmentID": ulid_field(),
        "common_name": make_nullable("string"),
        "technical_identifier": make_nullable("string"),
        "category": make_nullable("string"),
        "subcategory": make_nullable("string"),
        "country_of_origin": make_nullable("string"),
        "description": make_nullable("string"),
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "alternate_names": {"type": ["array", "null"], "items": {"type": "string"}},
        "variants": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "specifications": {"type": ["object", "null"]},
        # Narrative-sourced relationships to OTHER distinct equipment records
        # (predecessor/successor/variant). Inline sub-designations stay in `variants`;
        # these point to separate records. original_text retained for traceability.
        "related_equipment": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "relationship": enum_field(
                        ["predecessor", "successor", "variant"], nullable=True
                    ),
                    "name": make_nullable("string"),
                    "basis": make_nullable("string"),
                    "EquipmentID": ulid_field(nullable=True),
                    "original_text": make_nullable("string"),
                },
            },
        },
        "media": {"type": ["array", "object", "null"]},
        "external_data": {"type": ["object", "null"]},
        "extracted_date": make_nullable("string"),
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "MentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Sub_eventID": ulid_field(nullable=True),
                    "book": make_nullable("string"),
                    "context": make_nullable("string"),
                    # Retained verbatim source text — REQUIRED so the reusable
                    # SourceRechecker can gap-fill critical equipment fields
                    # (e.g. country_of_origin, a dedup veto) from the source
                    # rather than guessing externally. The extractor already
                    # captures this (equipment.py), but it must be declared here
                    # so retention is contractual (survives strict validation /
                    # any future additionalProperties tightening).
                    "original_text": make_nullable("string"),
                    # Per-mention operator — WHO used it here (may differ from the
                    # record's country_of_origin = design/manufacture origin).
                    # British-used US Shermans: operating_country=GBR, origin=USA.
                    # German-captured US gear: operating_country=DEU, captured=true,
                    # origin=USA. The dedup veto keys on ORIGIN only, never operator.
                    "operating_country": make_nullable("string"),
                    "captured": {"type": ["boolean", "null"]},
                    # Per-mention quantity: exact int + verbatim phrase. "10 Shermans"
                    # -> quantity 10, quantity_text "10". "several Shermans" ->
                    # quantity null, quantity_text "several" (vague counts preserved,
                    # never fabricated). Both null if no count stated.
                    "quantity": {"type": ["integer", "null"]},
                    "quantity_text": make_nullable("string"),
                    # Per-mention place (strongly preferred, not required). PlaceID is
                    # denormalized from the event's place link (like DateID).
                    "PlaceID": ulid_field(nullable=True),
                    "place_name": make_nullable("string"),
                    # How the source asserts presence. A mention exists ONLY when the
                    # source asserts the equipment was there: narrative text, or a
                    # video/audio narration that explicitly says so. Ambient/stock
                    # footage that does not assert presence is NOT extracted.
                    "assertion_source": enum_field(
                        ["narrative", "media_narration"], nullable=True
                    ),
                    "Event_Name": make_nullable("string"),
                    "Sub_event_Name": make_nullable("string"),
                    "DateID": ulid_field(nullable=True),
                    "DateMentionID": ulid_field(nullable=True),
                    "paragraph_numbers": {
                        "type": ["array", "null"],
                        "items": {"type": "integer"},
                    },
                    "variant_mentioned": make_nullable("string"),
                    "using_unit": {"type": ["object", "null"]},
                    "using_person": {"type": ["object", "null"]},
                },
            },
        },
        "enrichment_status": enum_field(["enriched", "not_found"], nullable=True),
        "openserp_searched": {"type": ["boolean", "null"]},
        "images": {"type": ["array", "null"], "items": {"type": "object"}},
    },
}
