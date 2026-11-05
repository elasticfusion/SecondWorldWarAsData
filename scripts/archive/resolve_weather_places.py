#!/usr/bin/env python3
"""Create place entities for unresolved weather locations and link them.

Reads weather files with null PlaceID, checks if a matching place already exists
(by name), creates new place entities for unmatched names, and updates weather files.
"""

import json
import logging
from pathlib import Path

import ulid

from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)
logging.basicConfig(level="INFO", format="%(message)s")


def main():
    output = Path("output")
    weather_dir = output / "weather"
    places_dir = output / "places"

    # Load existing place index (name → PlaceID)
    index_file = places_dir / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    # Build name lookup from all place files
    name_to_id = {}
    for pf in places_dir.glob("*.json"):
        if pf.name == "index.json":
            continue
        try:
            pd = json.loads(pf.read_text())
            pname = pd.get("place_name", pd.get("current_name", ""))
            pid = pd.get("PlaceID", "")
            if pname and pid:
                name_to_id[pname.lower()] = pid
        except Exception:
            continue

    created = 0
    linked = 0
    skipped = 0

    for wf in sorted(weather_dir.glob("*.json")):
        if wf.name == "index.json":
            continue
        data = json.loads(wf.read_text())
        loc = data.get("location", {})
        if loc.get("PlaceID"):
            continue  # Already resolved

        place_name = loc.get("place_name", "").strip()
        if not place_name:
            skipped += 1
            continue

        # Try to find existing place (case-insensitive)
        place_id = name_to_id.get(place_name.lower())

        if not place_id:
            # Create new place entity
            place_id = str(ulid.new())
            place_data = {
                "PlaceID": place_id,
                "place_name": place_name,
                "current_name": place_name,
                "identified_as": place_name,
                "place_type": "location",
                "latitude": loc.get("latitude", 0.0),
                "longitude": loc.get("longitude", 0.0),
                "country": "",
                "source": "weather_extraction",
                "event_mentions": data.get("event_mentions", []),
            }
            filename = place_name.lower().replace(" ", "_")[:60] + ".json"
            write_json_with_lock(places_dir / filename, place_data)
            name_to_id[place_name.lower()] = place_id
            # Update index
            index[place_name] = filename
            created += 1

        # Link weather to place
        data["location"]["PlaceID"] = place_id
        wf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        linked += 1

    # Save updated index
    write_json_with_lock(index_file, index)

    logger.info("Created %d new place entities", created)
    logger.info("Linked %d weather files to places", linked)
    logger.info("Skipped %d (no place_name)", skipped)


if __name__ == "__main__":
    main()
