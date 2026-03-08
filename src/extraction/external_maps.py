#!/usr/bin/env python3
"""
External Maps Extraction

Import external maps from YAML file and link to events/places/dates.
"""

# pylint: disable=W1203

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid
import yaml

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def load_yaml(yaml_path: Path) -> List[Dict[str, Any]]:
    """Load external maps from YAML file."""
    try:
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            logger.error(
                f"Invalid YAML structure in {yaml_path}: expected dict, got {type(data)}"
            )
            return []

        maps = data.get("maps", [])
        if not isinstance(maps, list):
            logger.error(
                f"Invalid 'maps' field in {yaml_path}: expected list, got {type(maps)}"
            )
            return []

        return maps

    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML file {yaml_path}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error reading {yaml_path}: {e}")
        return []


def find_event_from_place(
    place_keywords: List[str], places_dir: Path
) -> Optional[tuple[str, str, str, str]]:
    """Find event context from place keywords.

    Works backwards: place → event/sub-event (more efficient than searching all events)

    Returns: (EventID, Event_Name, Sub_eventID, Sub_event_Name) or None
    """
    if not places_dir.exists():
        return None

    for place_file in places_dir.glob("*.json"):
        try:
            with open(place_file, encoding="utf-8") as f:
                place_data = json.load(f)

            # Match against filename (format: PlaceName_ULID.json)
            filename = place_file.stem  # Remove .json
            place_name_from_file = filename.rsplit("_", 1)[0].replace("_", " ").lower()

            # Also check place_name field if present
            place_name_from_data = (place_data.get("place_name") or "").lower()

            # Match place keywords against either source
            if not any(
                kw.lower() in place_name_from_file or kw.lower() in place_name_from_data
                for kw in place_keywords
            ):
                continue

            # Get first non-null event mention
            event_mentions = place_data.get("event_mentions", [])
            for mention in event_mentions:
                if mention.get("Event_Name") and mention.get("Sub_event_Name"):
                    return (
                        mention.get("EventID"),
                        mention.get("Event_Name"),
                        mention.get("Sub_eventID"),
                        mention.get("Sub_event_Name"),
                    )

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"  Skipping place file {place_file.name}: {e}")
            continue
        except Exception as e:
            logger.debug(f"  Error reading {place_file.name}: {e}")
            continue

    return None


def find_place_mention_id(
    place_keywords: List[str], sub_event_id: str, places_dir: Path
) -> Optional[str]:
    """Find PlaceMentionID from keywords within event context."""
    for place_file in places_dir.glob("*.json"):
        try:
            with open(place_file, encoding="utf-8") as f:
                place_data = json.load(f)

            # Check if place is mentioned in this sub-event
            event_mentions = place_data.get("event_mentions", [])
            if not any(m.get("Sub_eventID") == sub_event_id for m in event_mentions):
                continue

            # Match keywords
            place_name = place_data.get("place_name", "").lower()
            if any(kw.lower() in place_name for kw in place_keywords):
                # Find the specific mention ID
                for mention in event_mentions:
                    if mention.get("Sub_eventID") == sub_event_id:
                        return mention.get("PlaceMentionID")

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"  Skipping place file {place_file.name}: {e}")
            continue
        except Exception as e:
            logger.debug(f"  Error reading {place_file.name}: {e}")
            continue

    return None


def find_date_match(date_str: str, sub_event_id: str, dates_dir: Path) -> Optional[str]:
    """Find DateMentionID from date within event context."""
    for date_file in dates_dir.glob("*.json"):
        try:
            with open(date_file, encoding="utf-8") as f:
                date_data = json.load(f)

            # Check if date matches
            if date_data.get("date") != date_str:
                continue

            # Check if date is mentioned in this sub-event
            event_mentions = date_data.get("event_mentions", [])
            for mention in event_mentions:
                if mention.get("Sub_eventID") == sub_event_id:
                    return mention.get("DateMentionID")

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"  Skipping date file {date_file.name}: {e}")
            continue
        except Exception as e:
            logger.debug(f"  Error reading {date_file.name}: {e}")
            continue

    return None


def create_map_record(
    map_data: Dict[str, Any],
    event_match: tuple[str, str, str, str],
    place_mention_id: Optional[str],
    date_mention_id: Optional[str],
    storage_backend: str,
) -> Dict[str, Any]:
    """Create map record with all metadata."""
    event_id, event_name, sub_event_id, sub_event_name = event_match
    map_id = str(ulid.new())

    record = {
        "MapID": map_id,
        "map_title": map_data["title"],
        "external_source": map_data["external_source"],
        "external_source_url": map_data["external_source_url"],
        "archive_id": map_data.get("archive_id"),
        "license": map_data["license"],
        "license_url": map_data.get("license_url"),
        "date_created": map_data.get("date_created"),
        "creator": map_data.get("creator"),
        "EventID": event_id,
        "Event_Name": event_name,
        "Sub_eventID": sub_event_id,
        "Sub_event_Name": sub_event_name,
        "place_name": map_data.get("place_keywords", [None])[0],
        "PlaceMentionID": place_mention_id,
        "date": map_data.get("date"),
        "DateMentionID": date_mention_id,
        "local_path": f"output/external_maps/{map_id}.json",
        "local_image_path": None,
        "source_url": map_data.get("file_url"),
        "file_format": None,
        "extracted_date": datetime.utcnow().isoformat() + "Z",
        "description": map_data.get("description"),
        "map_type": map_data.get("map_type"),
        "storage_backend": storage_backend,
        "found_via": map_data.get("found_via"),
        "found_date": map_data.get("found_date"),
    }

    return record


def _validate_required_fields(map_data: Dict[str, Any]) -> Optional[str]:
    """Validate required fields in map data.

    Returns: Error message if validation fails, None if valid
    """
    required = [
        "title",
        "external_source",
        "external_source_url",
        "license",
        "place_keywords",
    ]
    missing = [f for f in required if not map_data.get(f)]

    if missing:
        return f"Missing required fields: {', '.join(missing)}"

    if (
        not isinstance(map_data["place_keywords"], list)
        or not map_data["place_keywords"]
    ):
        return "place_keywords must be a non-empty list"

    return None


def _check_duplicate(
    output_dir: Path, sub_event_id: str, external_source_url: str
) -> bool:
    """Check if map already exists for this sub-event.

    Returns: True if duplicate found
    """
    for existing_file in output_dir.glob("*.json"):
        try:
            with open(existing_file, encoding="utf-8") as f:
                existing = json.load(f)

            if existing.get("Sub_eventID") == sub_event_id:
                # Check if same external source
                if existing.get("external_source_url") == external_source_url:
                    return True

        except Exception:
            continue

    return False


def _process_single_map(
    map_data: Dict[str, Any],
    output_dir: Path,
    places_dir: Path,
    dates_dir: Path,
    storage_backend: str,
    allowed_licenses: List[str],
) -> tuple[bool, str]:
    """Process a single map entry.

    Returns: (success: bool, message: str)
    """
    # Validate required fields
    validation_error = _validate_required_fields(map_data)
    if validation_error:
        return False, f"✗ {validation_error}"

    # Validate license
    license_type = map_data["license"]
    if license_type not in allowed_licenses:
        return False, f"✗ License '{license_type}' not in allowed list"

    # Required: Find event via place keywords
    place_keywords = map_data.get("place_keywords", [])
    if not place_keywords:
        return False, "✗ No place_keywords provided"

    event_match = find_event_from_place(place_keywords, places_dir)
    if not event_match:
        return False, f"✗ No place match for keywords: {place_keywords}"

    logger.info(f"  ✓ Event: {event_match[1]} / {event_match[3]}")

    # Get place mention ID
    place_mention_id = find_place_mention_id(place_keywords, event_match[2], places_dir)
    if place_mention_id:
        logger.info(f"  ✓ Place: {place_keywords[0]}")

    # Check for duplicate
    if _check_duplicate(
        output_dir, event_match[2], map_data.get("external_source_url", "")
    ):
        return False, "⚠ Map already exists for this sub-event"

    # Optional: Find date match
    date_mention_id = None
    date_str = map_data.get("date")
    if date_str and dates_dir.exists():
        date_mention_id = find_date_match(date_str, event_match[2], dates_dir)
        if date_mention_id:
            logger.info(f"  ✓ Date: {date_str}")

    # Create and save record
    record = create_map_record(
        map_data,
        event_match,
        place_mention_id,
        date_mention_id,
        storage_backend,
    )

    output_path = output_dir / f"{record['MapID']}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    return True, f"✓ Saved: {output_path.name}"


def import_maps(
    yaml_path: Path,
    output_dir: Path,
    places_dir: Path,
    dates_dir: Path,
    storage_backend: str = "filesystem",
    allowed_licenses: Optional[List[str]] = None,
) -> int:
    """Import external maps from YAML file.

    Returns: Number of maps successfully imported
    """
    # Validate directories exist
    if not places_dir.exists():
        logger.error(f"Places directory not found: {places_dir}")
        logger.error("Run phase2_extract.py first to extract places")
        return 0

    if not dates_dir.exists():
        logger.warning(f"Dates directory not found: {dates_dir}")
        logger.warning("Date linking will be skipped")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Default allowed licenses
    if allowed_licenses is None:
        allowed_licenses = ["Public Domain", "CC0", "CC-BY", "CC-BY-SA"]

    maps_data = load_yaml(yaml_path)
    if not maps_data:
        logger.warning("No maps found in YAML file")
        return 0

    imported = 0
    skipped = 0

    for map_data in maps_data:
        title = map_data.get("title", "Unknown")
        logger.info(f"\n📍 Processing: {title}")

        try:
            success, message = _process_single_map(
                map_data,
                output_dir,
                places_dir,
                dates_dir,
                storage_backend,
                allowed_licenses,
            )

            if success:
                logger.info(f"  {message}")
                imported += 1
            else:
                logger.error(f"  {message}")
                skipped += 1

        except (OSError, IOError) as e:
            logger.error(f"  ✗ File operation failed: {e}")
            skipped += 1
        except (KeyError, ValueError) as e:
            logger.error(f"  ✗ Data validation failed: {e}")
            skipped += 1

    # Summary
    if skipped > 0:
        logger.info(f"\n⚠ Skipped {skipped} map(s) due to errors")

    return imported


def main():
    """Main entry point."""
    project_root = Path(__file__).parent.parent
    yaml_path = project_root / "external_maps.yaml"

    if not yaml_path.exists():
        logger.error(f"✗ File not found: {yaml_path}")
        logger.info("\nCreate external_maps.yaml with map metadata.")
        logger.info("See contextmanagement/Specs/external_maps.md for format.")
        return

    output_dir = project_root / "output" / "external_maps"
    events_dir = project_root / "output" / "events"
    places_dir = project_root / "output" / "places"
    dates_dir = project_root / "output" / "dates"

    logger.info("=" * 60)
    logger.info("External Maps Import")
    logger.info("=" * 60)

    try:
        imported = import_maps(yaml_path, output_dir, events_dir, places_dir, dates_dir)
        logger.info(f"\n{'=' * 60}")
        logger.info(f"✓ Imported {imported} external maps")
        logger.info(f"{'=' * 60}")
    except Exception as e:
        logger.error(f"\n✗ Import failed: {e}")
        raise


if __name__ == "__main__":
    main()
