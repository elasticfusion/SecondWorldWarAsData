#!/usr/bin/env python3
"""Backfill missing map URLs and bounding boxes on place files."""

import json
import sys
from pathlib import Path


def _generate_map_urls(lat, lon):
    return {
        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12",
    }


def _calculate_bounding_box(lat, lon):
    return {
        "north": round(lat + 0.9, 4),
        "south": round(lat - 0.9, 4),
        "east": round(lon + 0.9, 4),
        "west": round(lon - 0.9, 4),
    }


def main():
    """Backfill map URLs and bounding boxes for places with coordinates."""
    dry_run = "--dry-run" in sys.argv
    places_dir = Path("output/places")
    skip = {"index.json", "duplicate_report.json", ".processed_events.json"}
    fixed = 0

    for f in places_dir.glob("*.json"):
        if f.name in skip:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        coords = data.get("coordinates", {})
        lat = coords.get("latitude", 0)
        lon = coords.get("longitude", 0)
        if not (lat and lon):
            continue

        changed = False
        if not data.get("map_urls"):
            data["map_urls"] = _generate_map_urls(lat, lon)
            changed = True
        if not data.get("bounding_box_100km"):
            data["bounding_box_100km"] = _calculate_bounding_box(lat, lon)
            changed = True

        if changed:
            if dry_run:
                print(f"  Would update: {f.name}")
            else:
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            fixed += 1

    print(f"{'Would fix' if dry_run else 'Fixed'} map URLs/bounding boxes on {fixed} files")


if __name__ == "__main__":
    main()
