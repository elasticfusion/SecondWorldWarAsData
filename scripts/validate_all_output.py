#!/usr/bin/env python3
"""Validate all output JSON files against strict schemas.

Reports pass/fail per entity type, identifies schema violations,
and flags files needing migration.
"""

import json
import sys
from pathlib import Path

from jsonschema import ValidationError, validate

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.schemas import SCHEMA_VERSION, needs_migration
from src.schemas.weather_output import WEATHER_OUTPUT_SCHEMA
from src.schemas.people_output import PEOPLE_OUTPUT_SCHEMA
from src.schemas.bibliography_output import BIBLIOGRAPHY_OUTPUT_SCHEMA
from src.schemas.places_output import PLACES_OUTPUT_SCHEMA
from src.schemas.groups_output import GROUPS_OUTPUT_SCHEMA
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA
from src.schemas.dates_output import DATES_OUTPUT_SCHEMA
from src.schemas.casualties_output import CASUALTIES_OUTPUT_SCHEMA
from src.schemas.logistics_output import LOGISTICS_OUTPUT_SCHEMA
from src.schemas.maps_output import MAPS_OUTPUT_SCHEMA
from src.schemas.events_output import EVENTS_OUTPUT_SCHEMA

ENTITY_CONFIGS = {
    "weather": {
        "path": "output/weather",
        "schema": WEATHER_OUTPUT_SCHEMA,
        "skip": {"index.json"},
    },
    "people": {
        "path": "output/people",
        "schema": PEOPLE_OUTPUT_SCHEMA,
        "skip": {"index.json", "duplicate_report.json", "not_duplicates.json"},
    },
    "bibliography": {
        "path": "output/bibliography",
        "schema": BIBLIOGRAPHY_OUTPUT_SCHEMA,
        "skip": {"index.json", "review_queue.json"},
    },
    "places": {
        "path": "output/places",
        "schema": PLACES_OUTPUT_SCHEMA,
        "skip": {"index.json", "duplicate_report.json", "not_duplicates.json"},
    },
    "groups": {
        "path": "output/people_groups",
        "schema": GROUPS_OUTPUT_SCHEMA,
        "skip": {
            "index.json",
            "duplicate_report.json",
            "not_duplicates.json",
            "not_related.json",
        },
    },
    "equipment": {
        "path": "output/equipment",
        "schema": EQUIPMENT_OUTPUT_SCHEMA,
        "skip": {
            "index.json",
            "duplicate_report.json",
            "not_duplicates.json",
            ".processed_events.json",
        },
    },
    "dates": {
        "path": "output/dates",
        "schema": DATES_OUTPUT_SCHEMA,
        "skip": {"index.json"},
    },
    "casualties": {
        "path": "output/casualties",
        "schema": CASUALTIES_OUTPUT_SCHEMA,
        "skip": {"index.json"},
    },
    "logistics": {
        "path": "output/logistics",
        "schema": LOGISTICS_OUTPUT_SCHEMA,
        "skip": {"index.json"},
    },
    "maps": {
        "path": "output/maps",
        "schema": MAPS_OUTPUT_SCHEMA,
        "skip": {"index.json", ".processed_events.json"},
    },
    "events": {
        "path": "output/content",
        "schema": EVENTS_OUTPUT_SCHEMA,
        "skip": {"index.json"},
        "pattern": "*-event.json",
    },
}

SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    "review_queue.json",
}


def validate_entity(name: str, config: dict) -> dict:
    """Validate all files for one entity type."""
    directory = Path(config["path"])
    schema = config["schema"]
    skip = config.get("skip", SKIP_FILES)

    results: dict = {"passed": 0, "failed": 0, "needs_migration": 0, "errors": []}

    if not directory.exists():
        return results

    pattern = config.get("pattern", "*.json")
    glob_fn = (
        directory.rglob
        if "/" in str(directory) or config.get("pattern")
        else directory.glob
    )
    for f in sorted(glob_fn(pattern)):
        if f.name in skip:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            results["failed"] += 1
            results["errors"].append(f"{f.name}: parse error — {e}")
            continue

        if needs_migration(data):
            results["needs_migration"] += 1

        try:
            validate(data, schema)
            results["passed"] += 1
        except ValidationError as e:
            results["failed"] += 1
            if len(results["errors"]) < 10:
                path_str = ".".join(str(p) for p in e.path)
                results["errors"].append(f"{f.name}: [{path_str}] {e.message[:120]}")

    return results


def main():
    print(f"=== Output Schema Validation (v{SCHEMA_VERSION}) ===\n")

    total_pass = total_fail = total_migrate = 0

    for name, config in ENTITY_CONFIGS.items():
        results = validate_entity(name, config)
        total = results["passed"] + results["failed"]
        total_pass += results["passed"]
        total_fail += results["failed"]
        total_migrate += results["needs_migration"]

        status = "✓" if results["failed"] == 0 else "✗"
        print(
            f"{status} {name:15s} {results['passed']:5d} pass | {results['failed']:5d} fail | {results['needs_migration']:5d} need migration | {total:5d} total"
        )

        for err in results["errors"][:3]:
            print(f"    {err}")

    # Cross-file Sub-eventID uniqueness check
    print(f"\n{'='*70}")
    print("Checking Sub-eventID uniqueness across event files...")
    dupes = _check_subevent_uniqueness()
    if dupes:
        total_fail += dupes
        print(f"✗ {dupes} duplicate IDs in event files")
    else:
        print("✓ All EventIDs and Sub-eventIDs are unique")

    # Entity ID uniqueness check
    print("\nChecking entity ID uniqueness...")
    entity_dupes = _check_entity_id_uniqueness()
    if entity_dupes:
        total_fail += entity_dupes
        print(f"✗ {entity_dupes} duplicate entity IDs found")
    else:
        print("✓ All entity IDs are unique")

    print(f"\n{'='*70}")
    print(
        f"Total: {total_pass} passed, {total_fail} failed, {total_migrate} need migration"
    )

    if total_fail > 0:
        sys.exit(1)


def _check_subevent_uniqueness() -> int:
    """Check that no EventID or Sub-eventID appears in multiple event files. Returns count of duplicates."""
    from collections import defaultdict

    eid_to_files = defaultdict(set)
    seid_to_files = defaultdict(set)
    within_file_dupes = 0
    for ef in Path("output/content").rglob("*-event.json"):
        if "notes" in ef.name:
            continue
        try:
            data = json.loads(ef.read_text(encoding="utf-8"))
            event = data.get("Event", data)
            eid = event.get("EventID")
            if eid:
                eid_to_files[eid].add(str(ef))
            seen_in_file = set()
            for se in event.get("Sub-events", []):
                sid = se.get("Sub-eventID")
                if sid:
                    if sid in seen_in_file:
                        within_file_dupes += 1
                    else:
                        seen_in_file.add(sid)
                    seid_to_files[sid].add(str(ef))
        except Exception:
            continue

    eid_dupes = {k: v for k, v in eid_to_files.items() if len(v) > 1}
    seid_dupes = {k: v for k, v in seid_to_files.items() if len(v) > 1}

    if within_file_dupes:
        print(f"    {within_file_dupes} within-file duplicate Sub-eventIDs")
    for sid, files in list(eid_dupes.items())[:3]:
        print(f"    EventID {sid}: {len(files)} files")
    for sid, files in list(seid_dupes.items())[:3]:
        print(f"    Sub-eventID {sid}: {len(files)} files")
    return len(eid_dupes) + len(seid_dupes) + within_file_dupes


if __name__ == "__main__":
    main()


def _check_entity_id_uniqueness() -> int:
    """Check all entity primary IDs and MentionIDs for uniqueness."""
    from collections import defaultdict

    CHECKS = [
        ("people", "PersonID"),
        ("people_groups", "PeopleGroupID"),
        ("people_groups", "GroupID"),
        ("places", "PlaceID"),
        ("equipment", "EquipmentID"),
        ("dates", "DateID"),
        ("bibliography", "BibliographyID"),
        ("casualties", "CasualtyID"),
        ("logistics", "LogisticsID"),
        ("maps", "MapID"),
    ]
    SKIP = {
        "index.json",
        "duplicate_report.json",
        "review_queue.json",
        "not_duplicates.json",
        "not_related.json",
        ".processed_events.json",
    }

    total_dupes = 0

    # Check primary IDs per entity type
    for dirname, id_field in CHECKS:
        d = Path(f"output/{dirname}")
        if not d.exists():
            continue
        ids = defaultdict(list)
        for f in d.glob("*.json"):
            if f.name in SKIP:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                eid = data.get(id_field)
                if eid:
                    ids[eid].append(f.name)
            except Exception:
                continue
        dupes = {k: v for k, v in ids.items() if len(v) > 1}
        if dupes:
            print(f"    {dirname}/{id_field}: {len(dupes)} duplicates")
            total_dupes += len(dupes)

    # Check MentionID uniqueness (within each file)
    mention_dupes = 0
    entity_dirs = [
        "people",
        "people_groups",
        "places",
        "equipment",
        "dates",
        "weather",
        "logistics",
        "casualties",
        "bibliography",
    ]
    for dirname in entity_dirs:
        d = Path(f"output/{dirname}")
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            if f.name in SKIP:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                mentions = data.get("event_mentions", [])
                seen = set()
                for m in mentions:
                    mid = m.get("MentionID")
                    if mid and mid in seen:
                        mention_dupes += 1
                        break  # count file once
                    if mid:
                        seen.add(mid)
            except Exception:
                continue

    if mention_dupes:
        print(f"    MentionID: {mention_dupes} files with within-file duplicates")
        total_dupes += mention_dupes

    return total_dupes
