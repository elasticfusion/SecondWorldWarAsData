#!/usr/bin/env python3
"""Merge duplicate date files into single records.

Groups by (date_start, date_end, time_start, time_end) — same temporal reference
gets one file with accumulated event_mentions. Updates cross-references in other
entity types to point to the surviving DateID.

Usage:
    python3 scripts/merge_dates.py [--dry-run]
"""

import argparse
import json
import glob
import os
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATES_DIR = Path("output/dates")
CROSS_REF_DIRS = ["output/weather", "output/casualties", "output/logistics"]


def make_key(d: dict) -> tuple:
    """Create dedup key from date fields."""
    return (
        d.get("date_start") or "",
        d.get("date_end") or "",
        d.get("time_start") or "",
        d.get("time_end") or "",
    )


def merge_group(files: list[tuple[Path, dict]]) -> tuple[dict, list[str]]:
    """Merge a group of duplicate date records. Returns (merged_record, deprecated_ids)."""
    # Sort by file mod time — keep earliest as canonical
    files.sort(key=lambda x: os.path.getmtime(x[0]))
    
    canonical_path, canonical = files[0]
    deprecated_ids = []
    seen_mentions = set()

    # Track existing mentions by content key
    for m in canonical.get("event_mentions", []):
        key = (m.get("Sub_eventID", m.get("Sub-eventID", "")), m.get("original_text", ""))
        seen_mentions.add(key)

    # Merge mentions from duplicates
    for path, dup in files[1:]:
        dup_id = dup.get("DateID", "")
        if dup_id and dup_id != canonical.get("DateID"):
            deprecated_ids.append(dup_id)
        for m in dup.get("event_mentions", []):
            key = (m.get("Sub_eventID", m.get("Sub-eventID", "")), m.get("original_text", ""))
            if key not in seen_mentions:
                canonical.setdefault("event_mentions", []).append(m)
                seen_mentions.add(key)

    return canonical, deprecated_ids


def update_cross_references(id_map: dict[str, str], dry_run: bool) -> int:
    """Update DateID/DateMentionID references in other entity files."""
    updated = 0
    for dir_path in CROSS_REF_DIRS:
        for f in glob.glob(os.path.join(dir_path, "*.json")):
            try:
                text = open(f).read()
                changed = False
                for old_id, new_id in id_map.items():
                    if old_id in text:
                        text = text.replace(old_id, new_id)
                        changed = True
                if changed:
                    if not dry_run:
                        with open(f, "w") as out:
                            out.write(text)
                    updated += 1
            except (OSError, json.JSONDecodeError):
                pass
    return updated


def main():
    parser = argparse.ArgumentParser(description="Merge duplicate date records")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Group files by dedup key
    groups = defaultdict(list)
    for f in sorted(DATES_DIR.glob("*.json")):
        if f.name == "index.json":
            continue
        try:
            d = json.load(open(f))
            if not isinstance(d, dict):
                continue
            key = make_key(d)
            groups[key].append((f, d))
        except (json.JSONDecodeError, OSError):
            pass

    total_files = sum(len(v) for v in groups.values())
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    dupe_files = sum(len(v) for v in dupes.values())

    logger.info(f"Total date files: {total_files}")
    logger.info(f"Unique date+time combos: {len(groups)}")
    logger.info(f"Groups with duplicates: {len(dupes)}")
    logger.info(f"Files to merge: {dupe_files}")
    logger.info("")

    id_map = {}  # old_id -> new_id
    files_removed = 0
    files_kept = 0

    for key, file_group in dupes.items():
        merged, deprecated_ids = merge_group(file_group)
        canonical_id = merged.get("DateID", "")

        # Map deprecated IDs to canonical
        for old_id in deprecated_ids:
            id_map[old_id] = canonical_id

        # Write merged record
        canonical_path = file_group[0][0]
        if not args.dry_run:
            with open(canonical_path, "w") as out:
                json.dump(merged, out, indent=2, ensure_ascii=False)

        # Remove duplicate files
        for path, _ in file_group[1:]:
            if not args.dry_run:
                path.unlink()
            files_removed += 1

        files_kept += 1

    # Update cross-references
    xref_updated = 0
    if id_map:
        logger.info(f"Updating cross-references ({len(id_map)} ID redirects)...")
        xref_updated = update_cross_references(id_map, args.dry_run)

    logger.info("")
    if args.dry_run:
        logger.info("DRY RUN — no changes made")
    logger.info(f"Merged groups: {len(dupes)}")
    logger.info(f"Files removed: {files_removed}")
    logger.info(f"Files kept (with merged mentions): {files_kept}")
    logger.info(f"Cross-ref files updated: {xref_updated}")
    logger.info(f"Final file count: {total_files - files_removed}")


if __name__ == "__main__":
    main()
