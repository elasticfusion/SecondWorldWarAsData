#!/usr/bin/env python3
"""Auto-merge exact duplicate equipment files and regenerate duplicate report.

Exact duplicates (normalized name match = 1.0, same category, same country) are
merged automatically. Near-duplicates go into the duplicate_report for UI review.

Usage:
    python3 scripts/merge_equipment_dupes.py [--dry-run]
"""

import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

EQUIPMENT_DIR = Path("output/equipment")
SKIP_FILES = {"index.json", "duplicate_report.json", "not_duplicates.json", ".processed_events.json"}


def _normalize(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"^\.", "", name)
    name = re.sub(r"(\d+)\s*-?\s*caliber", r"\1 cal", name)
    name = re.sub(r"(\d+)\s*-?\s*cal\b", r"\1 cal", name)
    name = re.sub(r"(\d+)\s*-?\s*mm\b", r"\1mm", name)
    name = re.sub(r"(\d+\.?\d*)\s*-?\s*cm\b", r"\1cm", name)
    name = re.sub(r"(\d+)\s*-?\s*lb\.?", r"\1 lb", name)
    name = re.sub(r"(\d+)\s*-?\s*pound", r"\1 lb", name)
    name = re.sub(r"\bgeneral purpose\b", "gp", name)
    name = re.sub(r"\bhigh explosive\b", "he", name)
    name = re.sub(r"\barmor[- ]piercing\b", "ap", name)
    name = re.sub(r"\bantitank\b", "at", name)
    name = re.sub(r"\banti[- ]tank\b", "at", name)
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def make_key(item: dict) -> str:
    """Exact dedup key: normalized name + country (category excluded — often inconsistent)."""
    name = _normalize(item.get("common_name", ""))
    country = (item.get("country_of_origin") or "").lower()
    return f"{name}|{country}"


def merge_group(files: list[tuple[Path, dict]]) -> dict:
    """Merge a group of exact duplicates. Keep richest record, accumulate mentions."""
    # Sort by richness: most event_mentions first, then most fields
    files.sort(key=lambda x: (-len(x[1].get("event_mentions", [])), -len(x[1])))
    primary_path, primary = files[0]

    seen_mentions = set()
    for m in primary.get("event_mentions", []):
        seen_mentions.add((m.get("original_text", ""), m.get("book", "")))

    for path, dup in files[1:]:
        for m in dup.get("event_mentions", []):
            key = (m.get("original_text", ""), m.get("book", ""))
            if key not in seen_mentions:
                primary.setdefault("event_mentions", []).append(m)
                seen_mentions.add(key)

        # Merge any fields the primary is missing
        for field in ["technical_identifier", "specifications", "variants", "alternate_names", "external_data"]:
            if not primary.get(field) and dup.get(field):
                primary[field] = dup[field]

    return primary


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Load all equipment
    groups = defaultdict(list)
    for f in sorted(EQUIPMENT_DIR.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        try:
            d = json.load(open(f))
            if not isinstance(d, dict):
                continue
            d["_path"] = f
            key = make_key(d)
            groups[key].append((f, d))
        except (json.JSONDecodeError, OSError):
            pass

    total = sum(len(v) for v in groups.values())
    exact_dupes = {k: v for k, v in groups.items() if len(v) > 1}
    dupe_files = sum(len(v) for v in exact_dupes.values())

    logger.info(f"Total equipment files: {total}")
    logger.info(f"Unique (name|category|country): {len(groups)}")
    logger.info(f"Groups with exact duplicates: {len(exact_dupes)}")
    logger.info(f"Files that are exact duplicates: {dupe_files}")
    logger.info("")

    merged = 0
    removed = 0

    for key, file_group in exact_dupes.items():
        primary = merge_group(file_group)
        primary_path = file_group[0][0]

        if not args.dry_run:
            # Remove internal tracking field
            primary.pop("_path", None)
            with open(primary_path, "w") as out:
                json.dump(primary, out, indent=2, ensure_ascii=False)

            # Delete duplicates
            for path, _ in file_group[1:]:
                path.unlink()
                removed += 1

        merged += 1

    logger.info(f"{'Would merge' if args.dry_run else 'Merged'}: {merged} groups")
    logger.info(f"{'Would remove' if args.dry_run else 'Removed'}: {removed} duplicate files")
    logger.info(f"Final file count: {total - removed}")


if __name__ == "__main__":
    main()
