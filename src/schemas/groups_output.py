"""Strict output schema for groups files (output/people_groups/*.json)."""

from src.schemas import (
    METADATA_PROPERTIES,
    entity_version,
    enum_field,
    make_nullable,
    ulid_field,
)

GROUPS_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": entity_version("people_groups"),
    "title": "Groups Output File",
    "type": "object",
    "required": ["GroupID"],
    "additionalProperties": False,
    "properties": {
        **METADATA_PROPERTIES,
        "GroupID": ulid_field(),
        "group_name": make_nullable("string"),
        "common_name": make_nullable("string"),
        "name": make_nullable("string"),
        "group_type": make_nullable("string"),
        "source_language": make_nullable("string"),
        "nationality": make_nullable("string"),
        "country_of_origin": make_nullable("string"),
        "alliance_membership": {
            "type": ["array", "string", "null"],
            "items": {"type": "string"},
        },
        "description": make_nullable("string"),
        "aliases": {"type": ["array", "null"], "items": {"type": "string"}},
        "parent_organization": make_nullable("string"),
        "sub_organizations": {"type": ["array", "null"], "items": {"type": "string"}},
        "member_countries": {"type": ["array", "null"], "items": {"type": "string"}},
        # --- Ephemeral / mission-scoped organizations (naval task forces, assault forces,
        # provisional commands). A task force is DATE-BOUND: assembled for a mission, then
        # dissolves — unlike a STANDING formation. Per the governing principle, dates MAY be
        # approximate or unresolved (flagged for supplemental); an unknown date never drops the
        # force. See docs/current/design/organizations-equipment-data-quality.md. ---
        "organization_lifecycle": enum_field(["standing", "ephemeral"], nullable=True),
        "active_period": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "start": make_nullable("string"),  # ISO date OR a fuzzy phrase ("D-Day")
                "end": make_nullable("string"),
                "precision": enum_field(
                    ["exact", "approximate", "unresolved"], nullable=True
                ),
                "original_text": make_nullable("string"),
            },
        },
        # Date-bound compositional membership: the standing units an ephemeral force
        # DELIVERED/CONTAINED for its mission (e.g. Force O -> ["1st Division"]). Distinct from
        # sub_organizations (permanent org chart); constituent_units is mission-scoped and must
        # NOT be flattened into the standing unit it carried (Force O != 1st Division).
        "constituent_units": {"type": ["array", "null"], "items": {"type": "string"}},
        "members": {
            "type": ["array", "null"],
            "items": {"type": ["string", "object"]},
        },
        "military_hierarchy": {"type": ["object", "array", "string", "null"]},
        "enrichment_data": {"type": ["object", "null"]},
        "images": {"type": ["array", "null"], "items": {"type": ["object", "string"]}},
        # Single highest-resolution, Grok-vision-confirmed unit patch / insignia,
        # downloaded + durably stored (ARTIFACT_STORE_DESIGN.md M0). Null until one
        # is confirmed (null-over-fake). local_path is relative to the artifacts/ store.
        "unit_patch": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "url": make_nullable("string"),
                "local_path": make_nullable("string"),
                "source": make_nullable("string"),
                "caption": make_nullable("string"),
                "vision_verified": {"type": ["boolean", "null"]},
                "width": {"type": ["integer", "null"]},
                "height": {"type": ["integer", "null"]},
                "sha256": make_nullable("string"),
            },
        },
        "wikipedia_url": make_nullable("string"),
        "wikipedia_extract": make_nullable("string"),
        "wikipedia_checked_at": make_nullable("string"),
        "grokipedia_url": make_nullable("string"),
        # OpenSERP (Phase 3) web results + veterans-association sites for the unit, and gate.
        "web_results": {
            "type": ["array", "null"],
            "items": {"type": "object", "additionalProperties": True},
        },
        "veterans_associations": {
            "type": ["array", "null"],
            "items": {"type": "object", "additionalProperties": True},
        },
        "openserp_searched": {"type": ["boolean", "null"]},
        "openserp_searched_at": {"type": ["number", "null"]},
        "event_mentions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "MentionID": ulid_field(),
                    "EventID": ulid_field(),
                    "Event_Name": make_nullable("string"),
                    "Sub_eventID": ulid_field(nullable=True),
                    "Sub_event_Name": make_nullable("string"),
                    "book": make_nullable("string"),
                    "author": make_nullable("string"),
                    "series": make_nullable("string"),
                    "context": make_nullable("string"),
                    "original_text": make_nullable("string"),
                    "role_in_event": make_nullable("string"),
                },
            },
        },
        "enrichment_status": enum_field(
            ["enriched", "not_found", "ambiguous"], nullable=True
        ),
        "enrichment_ambiguity_reason": {"type": ["string", "null"]},
        "last_enrichment_search": {"type": ["string", "null"]},
    },
}
