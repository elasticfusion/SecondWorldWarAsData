#!/usr/bin/env python3
"""Fix hand-crafted placeholder ULIDs in place hierarchy.parent_place_id.

Replaces fake IDs like '01ENGLAND0000000000000000' with real PlaceIDs
looked up from the places index, or removes them if no match exists.

Usage:
    python3 scripts/fix_fake_place_ulids.py [--dry-run]
"""

import json
import re
import sys
from pathlib import Path

_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
SKIP_FILES = frozenset(
    ["index.json", "duplicate_report.json", ".processed_events.json"]
)


def _build_name_to_id(places_dir: Path) -> dict[str, str]:
    """Build lowercase name → PlaceID lookup from all place files."""
    lookup = {}
    for f in places_dir.glob("*.json"):
        if f.name in SKIP_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid = data.get("PlaceID")
            name = data.get("current_name") or data.get("name", "")
            if pid and name:
                lookup[name.lower()] = pid
                for alias in data.get("aliases", []):
                    lookup[alias.lower()] = pid
        except (json.JSONDecodeError, OSError):
            pass
    return lookup


def _extract_name_from_fake_id(fake_id: str) -> str:
    """Extract the embedded name from a fake ULID like '01ENGLAND0000000000000000'."""
    # Strip leading digits and trailing zeros
    stripped = fake_id.lstrip("0123456789").rstrip("0")
    return stripped.lower()


def _fix_one_place(f: Path, name_to_id: dict, dry_run: bool) -> str:
    """Fix fake parent_place_id in one file. Returns 'fixed', 'removed', or 'skip'."""
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "skip"

    hierarchy = data.get("hierarchy")
    if not hierarchy:
        return "skip"

    parent_id = hierarchy.get("parent_place_id", "")
    if not parent_id or _ULID_PATTERN.match(parent_id):
        return "skip"

    embedded_name = _extract_name_from_fake_id(parent_id)
    real_id = name_to_id.get(embedded_name)

    if real_id:
        action = "would fix" if dry_run else "fixed"
        print(f"  {action}: {f.name} parent_place_id {parent_id} → {real_id}")
        hierarchy["parent_place_id"] = real_id
        result = "fixed"
    else:
        action = "would remove" if dry_run else "removed"
        print(f"  {action}: {f.name} parent_place_id {parent_id} (no match)")
        del hierarchy["parent_place_id"]
        result = "removed"

    if not dry_run:
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return result


def main():
    dry_run = "--dry-run" in sys.argv
    places_dir = Path("output/places")

    if not places_dir.exists():
        print("Error: output/places/ not found")
        sys.exit(1)

    name_to_id = _build_name_to_id(places_dir)
    print(f"Place name index: {len(name_to_id)} entries")

    fixed = 0
    removed = 0

    for f in sorted(places_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        result = _fix_one_place(f, name_to_id, dry_run)
        if result == "fixed":
            fixed += 1
        elif result == "removed":
            removed += 1

    prefix = "Would fix" if dry_run else "Fixed"
    print(f"\n{prefix} {fixed}, removed {removed} fake parent_place_id values")


if __name__ == "__main__":
    main()
