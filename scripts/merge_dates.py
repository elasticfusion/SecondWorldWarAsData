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
import os
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

DATES_DIR = Path("output/dates")


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

    # Track existing mentions by content key -> their surviving mention id.
    kept_mention_id: dict = {}
    for m in canonical.get("event_mentions", []):
        key = (
            m.get("Sub_eventID", m.get("Sub-eventID", "")),
            m.get("original_text", ""),
        )
        seen_mentions.add(key)
        mid = m.get("DateMentionID") or m.get("MentionID")
        if mid:
            kept_mention_id[key] = mid

    mention_id_map: dict = {}  # dropped/duplicate mention id -> surviving mention id

    # Merge mentions from duplicates
    for path, dup in files[1:]:
        dup_id = dup.get("DateID", "")
        if dup_id and dup_id != canonical.get("DateID"):
            deprecated_ids.append(dup_id)
        for m in dup.get("event_mentions", []):
            key = (
                m.get("Sub_eventID", m.get("Sub-eventID", "")),
                m.get("original_text", ""),
            )
            dmid = m.get("DateMentionID") or m.get("MentionID")
            if key not in seen_mentions:
                canonical.setdefault("event_mentions", []).append(m)
                seen_mentions.add(key)
                if dmid:
                    kept_mention_id[key] = dmid
            elif dmid and kept_mention_id.get(key) and kept_mention_id[key] != dmid:
                # identical mention deduped out -> redirect its id to the surviving one
                mention_id_map[dmid] = kept_mention_id[key]

    return canonical, deprecated_ids, mention_id_map


def update_cross_references(id_map: dict[str, str], dry_run: bool) -> int:
    """Redirect deprecated DateID/DateMentionID references to the survivor across ALL
    entity dirs + event files, via the hardened field-targeted helper
    ``src.dedup.merge.update_event_refs`` — NOT a blind text.replace (which could corrupt
    unrelated substrings and only scanned 3 dirs, leaving refs in equipment/maps/people/
    events dangling)."""
    if dry_run or not id_map:
        return len(id_map)
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    from src.dedup.merge import update_event_refs

    output_root = _P("output")
    for old_id, new_id in id_map.items():
        # ref_key "dates" covers sub-event date arrays; _replace_id_in_obj handles the
        # DateID/DateMentionID fields in entity files.
        update_event_refs(output_root, old_id, new_id, "dates")
    return len(id_map)


def _rebuild_index() -> None:
    """Rebuild output/dates/index.json (normalized key -> filename) from surviving files."""
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
    from src.extraction.dates import _normalize_date_key

    index: dict[str, str] = {}
    for f in sorted(DATES_DIR.glob("*.json")):
        if f.name == "index.json":
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ds = d.get("date_start")
        if not ds:
            continue
        key = _normalize_date_key(ds, d.get("time_start"))
        index[key] = f.name
    with open(DATES_DIR / "index.json", "w", encoding="utf-8") as out:
        json.dump(index, out, indent=2, ensure_ascii=False)
    logger.info(f"Rebuilt index.json ({len(index)} entries)")


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
        merged, deprecated_ids, mention_id_map = merge_group(file_group)
        canonical_id = merged.get("DateID", "")

        # Map deprecated DateIDs -> canonical, plus deduped-mention DateMentionID redirects.
        for old_id in deprecated_ids:
            id_map[old_id] = canonical_id
        id_map.update(mention_id_map)

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

    # Rebuild index.json from surviving files (merges deleted files the old index named).
    if not args.dry_run and files_removed:
        _rebuild_index()

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
