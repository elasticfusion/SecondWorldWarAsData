"""Schema infrastructure for output validation.

Every output JSON file carries:
  _schema_version: "2.3"    — which schema version wrote this file
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
from typing import Any, Dict

SCHEMA_VERSION = "2.3"

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


def inject_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """Stamp schema version and update date on a data dict before writing."""
    data["_schema_version"] = SCHEMA_VERSION
    data["_last_updated"] = date.today().isoformat()
    return data


def needs_migration(data: Dict[str, Any]) -> bool:
    """Check if a file needs migration to current schema version."""
    file_version = data.get("_schema_version", "0.0")
    return file_version != SCHEMA_VERSION


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
