#!/usr/bin/env python3
"""Reclassify military units from places/ to people_groups/.

Runs after Phase 2 extraction. Detects military unit patterns in place names
and moves them to people_groups/ with schema transformation.

Usage:
    python3 scripts/reclassify_military_units.py [--dry-run]
"""

import argparse
import json
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MILITARY_PATTERNS = re.compile(
    r"\b("
    r"division|corps|army|regiment|battalion|brigade|squadron|company|platoon|"
    r"infantry|armored|armoured|airborne|parachute|panzer|grenadier|cavalry|"
    r"artillery|engineer|reconnaissance|tank|headquarters|hq"
    r")\b",
    re.IGNORECASE,
)

# Names that contain military words but are actual places
FALSE_POSITIVES = {
    "infantry school",
    "historical division, wdss",
}

# Suffixes that indicate a geographic reference, not a unit
GEO_SUFFIXES = re.compile(
    r"\b(sector|zone|front|area|lodgment|beachhead|bridgehead)\s*$", re.IGNORECASE
)

SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
}


def is_military_unit(name: str) -> bool:
    """Check if a name is a military unit, not a place."""
    name_lower = name.lower().strip()
    if name_lower in FALSE_POSITIVES:
        return False
    if GEO_SUFFIXES.search(name):
        return False
    return bool(MILITARY_PATTERNS.search(name))


def transform_place_to_group(data: dict) -> dict:
    """Transform place schema to people_group schema."""
    name = data.get("current_name", data.get("name", ""))
    return {
        "GroupID": data.get("PlaceID", ""),
        "name": name,
        "group_name": name,
        "group_type": "military_unit",
        "source_language": data.get("source_language", "English"),
        "country_of_origin": data.get("country_of_origin", ""),
        "event_mentions": data.get("event_mentions", []),
        "aliases": data.get("aliases", data.get("historical_names", [])),
    }


def reclassify(output_root: Path, dry_run: bool = False) -> int:
    """Move military units from places/ to people_groups/. Returns count moved."""
    places_dir = output_root / "places"
    groups_dir = output_root / "people_groups"

    if not places_dir.exists():
        return 0

    moved = 0
    for f in sorted(places_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        name = data.get("current_name", data.get("name", f.stem.replace("_", " ")))
        if not is_military_unit(name):
            continue

        dest = groups_dir / f.name
        if dry_run:
            logger.info("[DRY RUN] %s → people_groups/", name)
        else:
            group_data = transform_place_to_group(data)
            groups_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                json.dumps(group_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            f.unlink()
            logger.info("  %s → people_groups/", name)
        moved += 1

    if moved:
        action = "[DRY RUN] " if dry_run else ""
        logger.info("%sReclassified %d military unit(s)", action, moved)
    return moved


def main():
    parser = argparse.ArgumentParser(
        description="Reclassify military units from places to groups"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    args = parser.parse_args()
    reclassify(args.output_root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
