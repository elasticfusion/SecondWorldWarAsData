#!/usr/bin/env python3
"""Migrate place JSON files from old schema to v2.0.0"""

import json
from pathlib import Path
from typing import Any, Dict

# Valid geography types in v2.0.0
VALID_GEOGRAPHY_TYPES = {
    "city",
    "town",
    "village",
    "country",
    "region",
    "province",
    "state",
    "sea",
    "ocean",
    "river",
    "lake",
    "mountain",
    "island",
    "peninsula",
    "continent",
    "military_base",
    "battlefield",
    "fortification",
    "bridge",
    "port",
    "airfield",
    "other",
}

# Mapping for common invalid types
TYPE_MAPPING = {
    "City": "city",
    "Country": "country",
    "Region": "region",
    "Sea": "sea",
    "Ocean": "ocean",
    "River": "river",
    "Lake": "lake",
    "Mountain": "mountain",
    "Island": "island",
    "unknown": "other",
    "Unknown": "other",
}


def migrate_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate a single place extraction item"""

    # Fix field names (hyphen to underscore)
    if "Sub-event_Name" in item:
        item["Sub_event_Name"] = item.pop("Sub-event_Name")
    if "Sub-eventID" in item:
        item["Sub_eventID"] = item.pop("Sub-eventID")

    # Process place mentions
    for place in item.get("Place_Mentions", []):
        # Fix geography type
        geo_type = place.get("geography_type")
        if geo_type:
            # Apply mapping
            if geo_type in TYPE_MAPPING:
                place["geography_type"] = TYPE_MAPPING[geo_type]
            # Lowercase if not in valid set
            elif geo_type.lower() in VALID_GEOGRAPHY_TYPES:
                place["geography_type"] = geo_type.lower()
            # Default to "other"
            elif geo_type not in VALID_GEOGRAPHY_TYPES:
                print(f"  ⚠ Unknown geography_type '{geo_type}' → 'other'")
                place["geography_type"] = "other"

        # Add coordinate precision if missing
        if "coordinate_precision" not in place and "latitude" in place:
            if geo_type in ["city", "town", "village"]:
                place["coordinate_precision"] = "approximate"
            else:
                place["coordinate_precision"] = "center_point"

        # Add confidence if missing
        if "confidence" not in place:
            place["confidence"] = 0.8

        # Add map URLs if coordinates exist
        if "map_urls" not in place and "latitude" in place and "longitude" in place:
            lat = place["latitude"]
            lon = place["longitude"]
            place["map_urls"] = {
                "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
                "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12",
            }

        # Process route stops if present
        if "route" in place and isinstance(place["route"], list):
            for stop in place["route"]:
                stop_type = stop.get("geography_type")
                if stop_type and stop_type in TYPE_MAPPING:
                    stop["geography_type"] = TYPE_MAPPING[stop_type]
                elif stop_type and stop_type.lower() in VALID_GEOGRAPHY_TYPES:
                    stop["geography_type"] = stop_type.lower()

                # Add map URLs for route stops
                if (
                    "map_urls" not in stop
                    and "latitude" in stop
                    and "longitude" in stop
                ):
                    lat = stop["latitude"]
                    lon = stop["longitude"]
                    stop["map_urls"] = {
                        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
                        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12",
                    }

    return item


def migrate_file(file_path: Path, dry_run: bool = False) -> bool:
    """Migrate a single place JSON file"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            print(f"✗ {file_path.name}: Not an array")
            return False

        # Migrate each item
        migrated_data = [migrate_item(item) for item in data]

        if not dry_run:
            # Write back
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(migrated_data, f, indent=2, ensure_ascii=False)

        print(f"✓ {file_path.name}")
        return True

    except json.JSONDecodeError as e:
        print(f"✗ {file_path.name}: Invalid JSON - {e}")
        return False
    except Exception as e:
        print(f"✗ {file_path.name}: Error - {e}")
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate place JSON files to schema v2.0.0"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without writing"
    )
    parser.add_argument(
        "--output-dir", default="output", help="Output directory to scan"
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    place_files = list(output_dir.rglob("*-places.json"))

    print(f"Found {len(place_files)} place files")
    if args.dry_run:
        print("DRY RUN - No files will be modified\n")
    else:
        print()

    success_count = 0
    for file_path in sorted(place_files):
        if migrate_file(file_path, dry_run=args.dry_run):
            success_count += 1

    print(
        f"\n{'✓' if success_count == len(place_files) else '⚠'} Migrated {success_count}/{len(place_files)} files"
    )

    if args.dry_run:
        print("\nRun without --dry-run to apply changes")


if __name__ == "__main__":
    main()
