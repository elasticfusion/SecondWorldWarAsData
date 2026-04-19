#!/usr/bin/env python3
"""Backfill spec-level fields on existing people_group files from enrichment_data."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extraction.enrich_groups import _promote_enrichment

SKIP = frozenset(
    [
        "index.json",
        "related_groups_report.json",
        ".processed_events.json",
        "not_related.json",
    ]
)


def main():
    dry_run = "--dry-run" in sys.argv
    groups_dir = Path("output/people_groups")
    updated = 0

    for group_file in sorted(groups_dir.glob("*.json")):
        if group_file.name in SKIP:
            continue

        with open(group_file, encoding="utf-8") as f:
            data = json.load(f)

        before = json.dumps(data, sort_keys=True)

        # Add group_name from name per spec
        if not data.get("group_name") and data.get("name"):
            data["group_name"] = data["name"]

        _promote_enrichment(data)

        if json.dumps(data, sort_keys=True) != before:
            if dry_run:
                print(f"  Would update: {group_file.name}")
            else:
                with open(group_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            updated += 1

    print(f"{'Would update' if dry_run else 'Updated'} {updated} files")


if __name__ == "__main__":
    main()
