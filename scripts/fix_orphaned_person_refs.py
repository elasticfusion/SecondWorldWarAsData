#!/usr/bin/env python3
"""Find and fix orphaned PersonID references in event files.

Scans all event Sub-events[].people[] arrays for PersonIDs that have no
matching file in output/people/. Reports orphans and optionally removes them.

Usage:
    python3 scripts/fix_orphaned_person_refs.py [--dry-run] [--verbose]
"""

import json
import sys
from pathlib import Path

SKIP_FILES = frozenset(
    [
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        ".processed_events.json",
    ]
)
ENTITY_DIRS = frozenset(
    [
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
    ]
)


def _load_valid_person_ids(people_dir: Path) -> set[str]:
    """Load all valid PersonIDs from people directory."""
    valid = set()
    if not people_dir.exists():
        return valid
    for f in people_dir.glob("*.json"):
        if f.name in SKIP_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid = data.get("PersonID")
            if pid:
                valid.add(pid)
        except (json.JSONDecodeError, OSError):
            pass
    return valid


def _find_event_files(output_root: Path) -> list[Path]:
    """Find all event files across book directories."""
    content_dir = output_root / "content"
    if content_dir.exists():
        return sorted(content_dir.rglob("*-event.json"))
    files = []
    for book_dir in sorted(output_root.iterdir()):
        if book_dir.is_dir() and book_dir.name not in ENTITY_DIRS:
            files.extend(sorted(book_dir.glob("*-event.json")))
    return files


def _clean_sub_event_people(se: dict, valid_ids: set[str], verbose: bool) -> int:
    """Remove orphaned PersonIDs from one sub-event. Returns count removed."""
    people = se.get("people", [])
    if not people:
        return 0
    clean = [pid for pid in people if pid in valid_ids]
    removed = len(people) - len(clean)
    if removed:
        if verbose:
            for pid in people:
                if pid not in valid_ids:
                    print(f"    orphan: {pid} in {se.get('Sub-eventID', '?')}")
        se["people"] = clean
    return removed


def _fix_event_file(
    event_file: Path, valid_ids: set[str], dry_run: bool, verbose: bool
):
    """Remove orphaned PersonIDs from one event file. Returns (orphaned_count, fixed)."""
    try:
        data = json.loads(event_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0, False

    event = data.get("Event", data)
    orphaned_count = sum(
        _clean_sub_event_people(se, valid_ids, verbose)
        for se in event.get("Sub-events", [])
    )

    if orphaned_count and not dry_run:
        event_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    return orphaned_count, orphaned_count > 0


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    output_root = Path("output")

    if not output_root.exists():
        print("Error: output/ directory not found")
        sys.exit(1)

    people_dir = output_root / "people"
    valid_ids = _load_valid_person_ids(people_dir)
    print(f"Valid PersonIDs: {len(valid_ids)}")

    event_files = _find_event_files(output_root)
    print(f"Event files: {len(event_files)}")

    total_orphaned = 0
    files_fixed = 0

    for event_file in event_files:
        orphaned, _ = _fix_event_file(event_file, valid_ids, dry_run, verbose)
        if orphaned:
            total_orphaned += orphaned
            files_fixed += 1
            action = "would fix" if dry_run else "fixed"
            print(f"  {action}: {event_file.name} ({orphaned} orphaned refs)")

    prefix = "Would remove" if dry_run else "Removed"
    print(
        f"\n{prefix} {total_orphaned} orphaned PersonID refs across {files_fixed} files"
    )


if __name__ == "__main__":
    main()
