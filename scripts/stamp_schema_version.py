#!/usr/bin/env python3
"""Stamp _schema_version and _last_updated on all output JSON files.

Run once to migrate all existing files to the versioned schema format.
Safe to re-run — only updates files that don't already have the current version.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schemas import SCHEMA_VERSION, inject_metadata, needs_migration

DIRS = [
    "output/weather",
    "output/people",
    "output/people_groups",
    "output/places",
    "output/equipment",
    "output/dates",
    "output/casualties",
    "output/logistics",
    "output/maps",
    "output/bibliography",
]

SKIP = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    "review_queue.json",
    ".processed_events.json",
}


def main():
    total = updated = skipped = errors = 0

    # Also handle event files
    event_files = (
        list(Path("output/content").rglob("*-event.json"))
        if Path("output/content").exists()
        else []
    )

    all_files = []
    for d in DIRS:
        p = Path(d)
        if p.exists():
            all_files.extend(f for f in p.glob("*.json") if f.name not in SKIP)
    all_files.extend(event_files)

    for f in sorted(all_files):
        total += 1
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            errors += 1
            continue

        if not needs_migration(data):
            skipped += 1
            continue

        inject_metadata(data)
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        updated += 1

    print(f"Schema version migration to v{SCHEMA_VERSION}")
    print(f"  Total files: {total}")
    print(f"  Updated:     {updated}")
    print(f"  Already current: {skipped}")
    print(f"  Errors:      {errors}")


if __name__ == "__main__":
    main()
