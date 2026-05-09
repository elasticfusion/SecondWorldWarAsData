#!/usr/bin/env python3
"""Clean up stale index.json entries across entity directories.

Removes:
  - Aliases that don't match the file's actual name field
  - Entries pointing to files that don't exist

Keeps:
  - The canonical entry (name matches file content)

Usage:
    python3 scripts/cleanup_indexes.py [--dry-run]
"""

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

ENTITIES = [
    ("people", "name"),
    ("people_groups", "group_name"),
    ("places", "current_name"),
    ("equipment", "common_name"),
]


def cleanup_index(entity_dir: Path, name_field: str, dry_run: bool) -> dict:
    """Clean one entity's index.json. Returns stats."""
    index_file = entity_dir / "index.json"
    if not index_file.exists():
        return {"skipped": True}

    index = json.loads(index_file.read_text(encoding="utf-8"))
    original_count = len(index)
    cleaned = {}
    removed_stale = 0
    removed_missing = 0

    # Build filename → actual name mapping
    file_names: dict[str, str] = {}
    for filename in set(index.values()):
        f = entity_dir / filename
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                actual = data.get(name_field, data.get("name", ""))
                file_names[filename] = actual.lower()
            except (json.JSONDecodeError, OSError):
                file_names[filename] = ""
        else:
            file_names[filename] = None  # missing

    for alias, filename in index.items():
        actual_name = file_names.get(filename)

        if actual_name is None:
            removed_missing += 1
            continue

        # Keep if alias matches actual name (case-insensitive, underscore-tolerant)
        alias_norm = alias.lower().replace("_", " ").strip()
        if alias_norm == actual_name or alias_norm == actual_name.replace("_", " "):
            cleaned[alias] = filename
        else:
            removed_stale += 1

    # Ensure every existing file has at least one index entry
    indexed_files = set(cleaned.values())
    for f in entity_dir.glob("*.json"):
        if f.name in (
            "index.json",
            "duplicate_report.json",
            "not_duplicates.json",
            "not_related.json",
            ".processed_events.json",
            "related_groups_report.json",
            "review_queue.json",
        ):
            continue
        if f.name not in indexed_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                name = data.get(name_field, data.get("name", ""))
                if name:
                    cleaned[name.lower()] = f.name
            except (json.JSONDecodeError, OSError):
                pass

    stats = {
        "original": original_count,
        "cleaned": len(cleaned),
        "removed_stale": removed_stale,
        "removed_missing": removed_missing,
    }

    if not dry_run and (removed_stale > 0 or removed_missing > 0):
        index_file.write_text(
            json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return stats


def main():
    parser = argparse.ArgumentParser(description="Clean stale index.json entries")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    args = parser.parse_args()

    for entity, name_field in ENTITIES:
        entity_dir = args.output_root / entity
        if not entity_dir.exists():
            continue
        stats = cleanup_index(entity_dir, name_field, args.dry_run)
        if stats.get("skipped"):
            continue
        action = "[DRY RUN] " if args.dry_run else ""
        logger.info(
            "%s%s: %d → %d entries (removed %d stale, %d missing)",
            action,
            entity,
            stats["original"],
            stats["cleaned"],
            stats["removed_stale"],
            stats["removed_missing"],
        )


if __name__ == "__main__":
    main()
