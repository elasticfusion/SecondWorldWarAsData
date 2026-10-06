"""Strict output schema for equipment files (output/equipment/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    SCHEMA_VERSION,
    enum_field,
    make_nullable,
    ulid_field,
)

# Structured specifications (Grokipedia/Wikipedia). Common fields named; tolerant of extra
# keys (no additionalProperties:false) so sources can add specs without a schema change.
_SPECIFICATIONS_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "weight": make_nullable("string"),
        "weight_kg": {"type": ["number", "string", "null"]},
        "speed": make_nullable("string"),
        "max_speed_kmh": {"type": ["number", "string", "null"]},
        "armament": make_nullable("string"),
        "main_armament": make_nullable("string"),
        "armor": make_nullable("string"),
        "crew": {"type": ["integer", "string", "null"]},
        "range": make_nullable("string"),
        "range_km": {"type": ["number", "string", "null"]},
    },
}

# Structured images (Grokipedia/Wikipedia/OpenSERP) with provenance + trust markers.
_IMAGES_SCHEMA = {
    "type": ["array", "null"],
    "items": {
        "type": "object",
        "properties": {
            "url": make_nullable("string"),
            "local_path": make_nullable("string"),
            "source": make_nullable("string"),
            "license": make_nullable("string"),
            "caption": make_nullable("string"),
            # representative (default — generic/stock, illustrates the TYPE) vs
            # documentary (source explicitly asserts it depicts this event).
            "image_scope": enum_field(["representative", "documentary"], nullable=True),
            # whether Grok vision confirmed the image depicts this equipment TYPE.
            "vision_verified": {"type": ["boolean", "null"]},
        },
    },
}

# Structured external-source data + provenance (Grokipedia/Wikipedia + museums/archives).
_EXTERNAL_DATA_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "grokipedia_url": make_nullable("string"),
        "wikipedia_url": make_nullable("string"),
        "additional_sources": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "source_type": make_nullable("string"),
                    "source_name": make_nullable("string"),
                    "url": make_nullable("string"),
                    "data_points": {
                        "type": ["array", "null"],
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": make_nullable("string"),
                                "value": make_nullable("string"),
                                "verified": {"type": ["boolean", "null"]},
                            },
                        },
                    },
                },
            },
        },
    },
}

# Crew accounts — NARRATIVE-sourced, person-linked. Source tracking is mandatory:
# original_text (verbatim) + book identify the origin of every account.
_CREW_ACCOUNTS_SCHEMA = {
    "type": ["array", "null"],
    "items": {
        "type": "object",
        "properties": {
            "PersonID": ulid_field(nullable=True),
            "person_name": make_nullable("string"),
            "role": make_nullable("string"),
            "observations": make_nullable("string"),
            "original_text": make_nullable("string"),
            "book": make_nullable("string"),
        },
    },
}

# Group A reference facts — ENRICHMENT-sourced (Grokipedia/Wikipedia). Each block carries
# its own `source` + `source_url` so even reference facts trace to where they came from.
_TIMELINE_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "first_production": make_nullable("string"),
        "first_combat_use": make_nullable("string"),
        "last_combat_use": make_nullable("string"),
        "total_produced": {"type": ["integer", "string", "null"]},
        "combat_losses": {"type": ["integer", "string", "null"]},
        "source": make_nullable("string"),
        "source_url": make_nullable("string"),
    },
}

_TECHNICAL_EVOLUTION_SCHEMA = {
    "type": ["array", "null"],
    "items": {
        "type": "object",
        "properties": {
            "date": make_nullable("string"),
            "change": make_nullable("string"),
            "reason": make_nullable("string"),
            "effectiveness": make_nullable("string"),
            "source": make_nullable("string"),
            "source_url": make_nullable("string"),
        },
    },
}

_LOGISTICS_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "fuel_consumption": make_nullable("string"),
        "ammunition_capacity": make_nullable("string"),
        "maintenance_hours_per_100_miles": {"type": ["number", "string", "null"]},
        "common_spare_parts": {"type": ["array", "null"], "items": {"type": "string"}},
        "supply_challenges": {"type": ["array", "null"], "items": {"type": "string"}},
        "source": make_nullable("string"),
        "source_url": make_nullable("string"),
    },
}

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
        # Canonical identity resolved by the disambiguator (exact/alias/fuzzy/Grok) so one
        # name is used across US/German/British designation systems; identity_source
        # records how it was resolved.
        "canonical_name": make_nullable("string"),
        "identity_source": enum_field(
            ["exact", "alias", "learned_alias", "fuzzy", "grok_disambiguation"],
            nullable=True,
        ),
        "category": make_nullable("string"),
        "subcategory": make_nullable("string"),
        "country_of_origin": make_nullable("string"),
        "description": make_nullable("string"),
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "alternate_names": {"type": ["array", "null"], "items": {"type": "string"}},
        # Variants managed INLINE in the same record file. Each variant may carry its own
        # specifications + images (M4A1 vs M4A3E8 differ). Tolerant: strings or objects
        # accepted (back-compat with the earlier loose shape).
        "variants": {
            "type": ["array", "null"],
            "items": {
                "type": ["string", "object"],
                "properties": {
                    "variant_name": make_nullable("string"),
                    "differences": make_nullable("string"),
                    "alternate_names": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                    "specifications": _SPECIFICATIONS_SCHEMA,
                    "images": _IMAGES_SCHEMA,
                },
            },
        },
        "specifications": _SPECIFICATIONS_SCHEMA,
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
        # Structured media/images (Grokipedia/Wikipedia/OpenSERP). `media` kept as a
        # tolerant alias of the legacy field; `images` is the structured first-class list.
        "media": {"type": ["array", "object", "null"]},
        "images": _IMAGES_SCHEMA,
        # Structured external-source data + provenance (Grokipedia/Wikipedia + others).
        "external_data": _EXTERNAL_DATA_SCHEMA,
        # Proposal-backlog fields (all source-tracked):
        "crew_accounts": _CREW_ACCOUNTS_SCHEMA,  # narrative-sourced (original_text+book)
        # Condition-linked performance (weather/terrain -> effect), narrative-sourced.
        # Distinct from the ambient Weather entity. e.g. M4 in sub-zero temps.
        "environmental_performance": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "condition": make_nullable("string"),
                    "effect": make_nullable("string"),
                    "original_text": make_nullable("string"),
                },
            },
        },
        "timeline": _TIMELINE_SCHEMA,  # enrichment-sourced (source+source_url)
        "technical_evolution": _TECHNICAL_EVOLUTION_SCHEMA,  # enrichment-sourced
        "logistics": _LOGISTICS_SCHEMA,  # enrichment-sourced
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
        # Epoch of the last Grokipedia/Wikipedia enrichment CHECK (staleness gate +
        # diff). Stamped on every check (success, no-op, or failure) to limit re-checks.
        "enrichment_checked_at": {"type": ["integer", "null"]},
        "openserp_searched": {"type": ["boolean", "null"]},
    },
}
