"""Schema infrastructure for output validation.

Every output JSON file carries:
  _schema_version: "2.4"    — which schema version wrote this file
  _last_updated: "2026-05-09"  — when the file was last modified

When a schema evolves:
1. Bump SCHEMA_VERSION
2. Add a migration function in migrations.py
3. validate_all_output.py detects version mismatch and offers migration

Validation rules:
- Required fields must always be present (never null unless explicitly typed as nullable)
- Optional fields may be absent OR null
- When optional fields ARE present and non-null, they must match the defined type/format
- additionalProperties: false — no undocumented fields allowed
- This catches data corruption, malformed enrichment, and schema drift
"""

from datetime import date
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Per-entity schema versions, CENTRALLY MANAGED.
#
# Each entity type carries its OWN version so a change to one entity does not ripple to the
# others (no cross-entity coupling, nothing "left behind"). This single map is the source of
# truth: schema modules, extractor SCHEMA_TARGETs, and the schema guards all derive their
# version from here. Bump exactly the entity you changed.
#
# Compatibility rule (owner-directed):
#   • ADDITIVE change (new OPTIONAL field, widened type) -> bump that entity + re-pin its
#     fingerprint. NO reprocessing: existing records remain valid (the consistency test
#     confirms it).
#   • BREAKING change (new REQUIRED field, removed/renamed field, tightened type) -> bump
#     that entity AND either register_upgrade(old,new) or run a TARGETED reprocess of that
#     one entity. Never a global reprocess; other entities are untouched.
# ---------------------------------------------------------------------------
ENTITY_SCHEMA_VERSIONS: Dict[str, str] = {
    "events": "2.24",
    "dates": "2.24",
    "places": "2.25",
    "people": "2.24",
    "people_groups": "2.27",
    "equipment": "2.25",
    "weather": "2.24",
    "logistics": "2.24",
    "casualties": "2.24",
    "maps": "2.24",
    "map_features": "2.24",
    "bibliography": "2.24",
    "images": "2.25",
    "source_section": "2.26",
}


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except (TypeError, ValueError):
        return (0,)


def entity_version(entity: Optional[str]) -> str:
    """Return the schema version for an entity. Unknown/None falls back to the MIN version
    across all entities — a conservative default that never stamps a record ABOVE its real
    entity's version (which would make the schema contract wrongly skip it as 'future').
    Callers that know their entity should always pass it."""
    if entity and entity in ENTITY_SCHEMA_VERSIONS:
        return ENTITY_SCHEMA_VERSIONS[entity]
    return min(ENTITY_SCHEMA_VERSIONS.values(), key=_version_tuple)


# Deprecated global alias (= highest per-entity version). Kept so un-migrated callers keep
# working; new code should use entity_version(<entity>).
SCHEMA_VERSION = max(ENTITY_SCHEMA_VERSIONS.values(), key=_version_tuple)

# Shared patterns
ULID_PATTERN = "^[0-9A-HJKMNP-TV-Z]{26}$"
DATE_PATTERN = "^\\d{4}-\\d{2}-\\d{2}$"
DATE_MONTH_PATTERN = "^\\d{4}-\\d{2}(-\\d{2})?$"
URL_PATTERN = "^https?://"

# Metadata fields injected into every output file
METADATA_PROPERTIES = {
    "_schema_version": {"type": "string"},
    "_last_updated": {"type": "string", "pattern": DATE_PATTERN},
}


def inject_metadata(
    data: Dict[str, Any], entity: Optional[str] = None
) -> Dict[str, Any]:
    """Stamp the entity's schema version + update date before writing. `entity` selects the
    per-entity version; when omitted, falls back to the max version (safe default — write
    paths thread the entity in incrementally)."""
    data["_schema_version"] = entity_version(entity)
    data["_last_updated"] = date.today().isoformat()
    return data


def needs_migration(data: Dict[str, Any], entity: Optional[str] = None) -> bool:
    """True if a record's stamped version differs from its entity's current version."""
    file_version = data.get("_schema_version", "0.0")
    return file_version != entity_version(entity)


def make_nullable(type_name: str):
    """Helper: make a type nullable."""
    return {"type": [type_name, "null"]}


def ulid_field(nullable: bool = False):
    """Helper: ULID string field."""
    if nullable:
        return {"type": ["string", "null"], "pattern": ULID_PATTERN}
    return {"type": "string", "pattern": ULID_PATTERN}


def date_field(nullable: bool = False, allow_month: bool = False):
    """Helper: date string field."""
    pattern = DATE_MONTH_PATTERN if allow_month else DATE_PATTERN
    if nullable:
        return {"type": ["string", "null"], "pattern": pattern}
    return {"type": "string", "pattern": pattern}


def enum_field(values: list, nullable: bool = False):
    """Helper: enum field."""
    if nullable:
        return {"type": ["string", "null"], "enum": values + [None]}
    return {"type": "string", "enum": values}


def url_field(nullable: bool = False):
    """Helper: URL string field."""
    if nullable:
        return {"type": ["string", "null"], "pattern": URL_PATTERN}
    return {"type": "string", "pattern": URL_PATTERN}


# Event mentions array — shared across many entity types
EVENT_MENTIONS_SCHEMA = {
    "type": ["array", "null"],
    "items": {
        "type": "object",
        "properties": {
            "EventID": ulid_field(),
            "Sub-eventID": ulid_field(),
            "book": {"type": ["string", "null"]},
            "chapter": {"type": ["string", "null"]},
        },
    },
}
