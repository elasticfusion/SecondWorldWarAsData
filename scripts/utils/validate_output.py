#!/usr/bin/env python3
"""Validate all output JSON files against their schemas.

Checks every entity type in output/ for structural correctness,
valid ULIDs, required fields, and type consistency.
"""

import json
import re
import sys
from pathlib import Path

_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# ---------------------------------------------------------------------------
# Per-file schemas: {required_fields: {field: type_check_fn}, id_field: str}
# type_check_fn returns True if value is acceptable
# ---------------------------------------------------------------------------


def _is_str(v):
    return isinstance(v, str) and len(v) > 0


def _is_list(v):
    return isinstance(v, list)


def _is_ulid(v):
    return isinstance(v, str) and bool(_ULID.match(v))


SCHEMAS = {
    "event": {
        "id_field": "EventID",
        "required": {"EventID": _is_ulid, "Sub-events": _is_list},
        "path": "output/{book}/*-event.json",
        "nested": True,  # wrapped in {"Event": {...}}
    },
    "date": {
        "id_field": "DateID",
        "required": {"DateID": _is_ulid, "date": _is_str, "event_mentions": _is_list},
    },
    "place": {
        "id_field": "PlaceID",
        "required": {"PlaceID": _is_ulid, "name": _is_str, "event_mentions": _is_list},
    },
    "person": {
        "id_field": "PersonID",
        "required": {"PersonID": _is_ulid, "name": _is_str, "event_mentions": _is_list},
    },
    "people_group": {
        "id_field": "GroupID",
        "required": {"GroupID": _is_ulid, "name": _is_str, "event_mentions": _is_list},
    },
    "equipment": {
        "id_field": None,
        "required": {"common_name": _is_str, "category": _is_str},
    },
    "casualty": {
        "id_field": "CasualtyID",
        "required": {"CasualtyID": _is_ulid, "type": _is_str, "description": _is_str},
    },
    "weather": {
        "id_field": "WeatherID",
        "required": {"WeatherID": _is_ulid, "date": _is_str},
    },
    "logistics": {
        "id_field": "LogisticsID",
        "required": {
            "LogisticsID": _is_ulid,
            "logistics_type": _is_str,
            "category": _is_str,
        },
    },
    "map": {
        "id_field": "MapID",
        "required": {"MapID": _is_ulid, "map_title": _is_str},
    },
    "bibliography": {
        "id_field": "BibliographyID",
        "required": {
            "BibliographyID": _is_ulid,
            "title": _is_str,
            "mentions": _is_list,
        },
    },
    "supplemental": {
        "id_field": "EventID",
        "required": {"EventID": _is_ulid, "Supplemental_Material": _is_list},
        "array_of_objects": True,  # old format: [{...}, {...}]
    },
}

# Map entity type → directory and glob pattern
ENTITY_DIRS = {
    "date": ("output/dates", "*.json"),
    "place": ("output/places", "*.json"),
    "person": ("output/people", "*.json"),
    "people_group": ("output/people_groups", "*.json"),
    "equipment": ("output/equipment", "*.json"),
    "casualty": ("output/casualties", "*.json"),
    "weather": ("output/weather", "*.json"),
    "logistics": ("output/logistics", "*.json"),
    "map": ("output/maps", "*.json"),
    "bibliography": ("output/bibliography", "*.json"),
}

SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "related_groups_report.json",
    "not_duplicates.json",
    "review_queue.json",
    ".processed_events.json",
}


def _check_required_fields(data, required):
    """Check required fields against type validators. Returns error list."""
    errors = []
    for field, check in required.items():
        if field not in data:
            errors.append(f"Missing required field: {field}")
        elif not check(data[field]):
            val_repr = str(data[field])[:60]
            errors.append(
                f"Invalid value for {field}: {type(data[field]).__name__} = {val_repr}"
            )
    return errors


def _check_ulids(data):
    """Validate ULIDs in event_mentions and sub-events. Returns error list."""
    errors = []
    for mention in data.get("event_mentions", []):
        if isinstance(mention, dict):
            mid = mention.get("MentionID", "")
            if mid and not _ULID.match(mid):
                errors.append(f"Invalid MentionID ULID: {mid}")
    for se in data.get("Sub-events", []):
        if isinstance(se, dict):
            seid = se.get("Sub-eventID", "")
            if seid and not _ULID.match(seid):
                errors.append(f"Invalid Sub-eventID ULID: {seid}")
    return errors


def validate_file(filepath: Path, schema: dict) -> list[str]:
    """Validate a single JSON file. Returns list of error strings."""
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]

    if schema.get("nested"):
        if "Event" in data:
            data = data["Event"]
        else:
            return ["Missing 'Event' wrapper key"]

    if schema.get("array_of_objects"):
        if not isinstance(data, list):
            return [f"Expected array, got {type(data).__name__}"]
        errors = []
        for i, item in enumerate(data):
            for field, check in schema["required"].items():
                if field not in item:
                    errors.append(f"Item {i}: missing required field: {field}")
                elif not check(item[field]):
                    errors.append(f"Item {i}: invalid {field}")
        return errors

    return _check_required_fields(data, schema["required"]) + _check_ulids(data)


def find_event_files(output_root: Path) -> list[Path]:
    """Find all event files across book directories."""
    # New layout: output/content/{Book}/*-event.json
    content_dir = output_root / "content"
    if content_dir.exists():
        return sorted(content_dir.rglob("*-event.json"))
    # Old layout: output/{Book}/*-event.json (exclude entity dirs)
    files = []
    for book_dir in output_root.iterdir():
        if book_dir.is_dir() and book_dir.name not in {
            "dates",
            "places",
            "people",
            "people_groups",
            "equipment",
            "casualties",
            "weather",
            "logistics",
            "maps",
            "maps_images",
            "bibliography",
            "supplemental",
        }:
            files.extend(book_dir.glob("*-event.json"))
    return sorted(files)


def find_supplemental_files(output_root: Path) -> list[Path]:
    """Find all supplemental files across book directories."""
    content_dir = output_root / "content"
    if content_dir.exists():
        return sorted(content_dir.rglob("*-notes-event.json"))
    files = []
    for book_dir in output_root.iterdir():
        if book_dir.is_dir() and book_dir.name not in {
            "dates",
            "places",
            "people",
            "people_groups",
            "equipment",
            "casualties",
            "weather",
            "logistics",
            "maps",
            "maps_images",
            "bibliography",
            "supplemental",
        }:
            files.extend(book_dir.glob("*-notes-event.json"))
    return sorted(files)


def _validate_file_list(files, schema, label, totals, all_errors):
    """Validate a list of files, update totals, print summary line."""
    errors = []
    for f in files:
        totals["total"] += 1
        errs = validate_file(f, schema)
        if errs:
            totals["invalid"] += 1
            errors.append((str(f), errs))
        else:
            totals["valid"] += 1
    if errors:
        all_errors[label] = errors
    n_valid = len(files) - len(errors)
    print(
        f"  {label + ':':<16} {len(files):>5} files, {n_valid:>5} valid, {len(errors):>3} invalid"
    )


def _print_errors(all_errors):
    """Print error details."""
    print(f"\n{'=' * 50}")
    print("ERRORS:")
    for entity_type, file_errors in all_errors.items():
        print(f"\n  [{entity_type}] ({len(file_errors)} files)")
        for filepath, errs in file_errors[:5]:
            print(f"    {Path(filepath).name}:")
            for err in errs[:3]:
                print(f"      - {err}")
        if len(file_errors) > 5:
            print(f"    ... and {len(file_errors) - 5} more files")


def _collect_entity_files(entity_dir, pattern):
    """Collect JSON files from a directory, skipping metadata files."""
    return [
        f
        for f in sorted(entity_dir.glob(pattern))
        if f.is_file() and f.name not in SKIP_FILES
    ]


def _validate_entity_dirs(totals, all_errors):
    """Validate all flat entity directories."""
    for entity_type, (dir_path, pattern) in ENTITY_DIRS.items():
        entity_dir = Path(dir_path)
        if not entity_dir.exists():
            continue
        files = _collect_entity_files(entity_dir, pattern)
        _validate_file_list(
            files, SCHEMAS[entity_type], entity_type, totals, all_errors
        )

    supp_dir = Path("output/supplemental")
    if supp_dir.exists():
        files = [f for f in sorted(supp_dir.glob("*.json")) if f.is_file()]
        _validate_file_list(
            files, SCHEMAS["supplemental"], "supplemental", totals, all_errors
        )


def main():
    output_root = Path("output")
    if not output_root.exists():
        print("Error: output/ directory not found")
        sys.exit(1)

    totals = {"total": 0, "valid": 0, "invalid": 0}
    all_errors: dict[str, list[tuple[str, list[str]]]] = {}

    _validate_file_list(
        find_event_files(output_root), SCHEMAS["event"], "event", totals, all_errors
    )
    supp_files = find_supplemental_files(output_root)
    if supp_files:
        _validate_file_list(
            supp_files, SCHEMAS["event"], "notes-event", totals, all_errors
        )

    _validate_entity_dirs(totals, all_errors)

    print(f"\n{'=' * 50}")
    print(
        f"TOTAL: {totals['total']} files, {totals['valid']} valid, {totals['invalid']} invalid"
    )

    if all_errors:
        _print_errors(all_errors)

    sys.exit(1 if totals["invalid"] > 0 else 0)


if __name__ == "__main__":
    print("Validating output/ ...\n")
    main()
