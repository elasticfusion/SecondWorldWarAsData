"""Place extraction from event data."""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import ulid
from pydantic import BaseModel, ConfigDict, Field

from src.grok_client import GrokClient
from src.utils.file_lock import write_json_with_lock
from src.utils.json_validator import _fix_invalid_ulids

logger = logging.getLogger(__name__)


# Pydantic schemas for structured outputs
class MapUrls(BaseModel):
    """Map service URLs."""

    google_maps: str = Field(description="Google Maps URL")
    openstreetmap: str = Field(description="OpenStreetMap URL")


class PlaceMention(BaseModel):
    """Individual place mention."""

    PlaceMentionID: str = Field(description="26-character ULID")
    current_name: str = Field(description="Current name of the place")
    historical_name: Optional[str] = Field(
        default=None, description="Historical name if different"
    )
    source_language: str = Field(
        default="English", description="Language of source text"
    )
    latitude: float = Field(
        description="Latitude coordinate (use geographic center for large regions)"
    )
    longitude: float = Field(
        description="Longitude coordinate (use geographic center for large regions)"
    )
    geography_type: str = Field(description="Type: city, country, region, sea, etc.")
    date_context: Optional[str] = Field(
        default=None, description="Date context if mentioned"
    )
    role_in_event: Optional[str] = Field(
        default=None,
        description="Role of place in event (e.g., 'target of attack', 'defensive position')",
    )
    original_text: str = Field(description="Exact text from document")
    map_urls: Optional[MapUrls] = Field(
        default=None, description="Modern map service URLs"
    )


class PlaceOutput(BaseModel):
    """Place extraction output."""

    Event_Name: str = Field(description="Name of the event")
    EventID: str = Field(description="26-character ULID of event")
    Sub_event_Name: str = Field(
        description="Name of the sub-event", alias="Sub-event_Name"
    )
    Sub_eventID: str = Field(
        description="26-character ULID of sub-event", alias="Sub-eventID"
    )
    Place_Mentions: list[PlaceMention] = Field(description="List of place mentions")

    model_config = ConfigDict(populate_by_name=True)


SYSTEM_PROMPT = """You are an expert historian and geographer analyzing World War II documents.
Extract all place mentions from the provided event text with accurate coordinates.
For large regions (oceans, continents, fronts), use the geographic center point.
Return structured data matching the schema."""


@lru_cache(maxsize=1000)
def _calculate_bounding_box(lat: float, lon: float) -> Dict[str, float]:
    """Calculate 100km bounding box around coordinates."""
    return {
        "north": round(lat + 0.9, 4),
        "south": round(lat - 0.9, 4),
        "east": round(lon + 0.9, 4),
        "west": round(lon - 0.9, 4),
    }


def _generate_map_urls(lat: float, lon: float) -> Dict[str, str]:
    """Generate map service URLs for coordinates."""
    return {
        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12",
    }


def _is_valid_place_mention(mention: Dict[str, Any]) -> bool:
    """Check if place mention has required fields."""
    if mention.get("current_name") is None:
        logger.debug("Removed place mention with null current_name")
        return False

    # Skip places without coordinates (unless they're routes)
    if "route" not in mention:
        if mention.get("latitude") is None or mention.get("longitude") is None:
            logger.debug(
                "Removed place '%s' with null coordinates", mention.get("current_name")
            )
            return False

    return True


def _add_geo_data(mention: Dict[str, Any], lat: float, lon: float) -> None:
    """Add bounding box and map URLs to a place mention."""
    mention["bounding_box"] = _calculate_bounding_box(lat, lon)
    if "map_urls" not in mention:
        mention["map_urls"] = _generate_map_urls(lat, lon)


def _process_place_mention(mention: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single place mention: fix nulls and add bounding boxes."""
    if mention.get("geography_type") is None:
        mention["geography_type"] = "Unknown"
        logger.debug("Fixed null geography_type to 'Unknown'")

    # Add bounding box and map URLs for regular places
    if mention.get("latitude") and mention.get("longitude"):
        _add_geo_data(mention, mention["latitude"], mention["longitude"])

    # Process route stops
    if "route" in mention and isinstance(mention["route"], list):
        for stop in mention["route"]:
            if stop.get("latitude") and stop.get("longitude"):
                _add_geo_data(stop, stop["latitude"], stop["longitude"])

    return mention


def _process_place_mentions(data: Dict[str, Any]) -> None:
    """Process and filter place mentions in-place."""
    if "Place_Mentions" in data and isinstance(data["Place_Mentions"], list):
        valid_mentions = []
        for mention in data["Place_Mentions"]:
            if isinstance(mention, dict) and _is_valid_place_mention(mention):
                valid_mentions.append(_process_place_mention(mention))
        data["Place_Mentions"] = valid_mentions


def _fix_null_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Fix null values in required fields and add bounding boxes."""
    _process_place_mentions(data)

    for key, value in data.items():
        if isinstance(value, dict):
            data[key] = _fix_null_fields(value)
        elif isinstance(value, list):
            data[key] = [
                _fix_null_fields(item) if isinstance(item, dict) else item
                for item in value
            ]
    return data


def create_place_prompt(
    sub_event: Dict[str, Any], event_id: str, event_name: str
) -> str:
    """Create prompt for place extraction from a sub-event."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    sub_event_summary = sub_event.get("Sub-event_summary", "")
    fulltext = sub_event.get("Sub-event_fulltext", {})

    text_parts = []
    for key in sorted(fulltext.keys()):
        text_parts.append(fulltext[key])
    text = "\n\n".join(text_parts)

    prompt = f"""Extract all place mentions from this WWII text with coordinates.
For large geographic features (oceans, continents, military fronts), provide the geographic center coordinates.

Event: {event_name} (ID: {event_id})
Sub-event: {sub_event_summary} (ID: {sub_event_id})

Text:
{text}

Return JSON matching this structure:
{{
  "Event_Name": "{event_name}",
  "EventID": "{event_id}",
  "Sub_event_Name": "{sub_event_summary}",
  "Sub_eventID": "{sub_event_id}",
  "Place_Mentions": [
    {{
      "PlaceMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
      "current_name": "Normandy",
      "historical_name": null,
      "source_language": "English",
      "latitude": 49.18,
      "longitude": -0.37,
      "geography_type": "region",
      "date_context": "June 1944",
      "role_in_event": "location of Allied invasion",
      "original_text": "Normandy"
    }}
  ]
}}

Instructions:
- date_context: Extract any date/time context mentioned with this place (e.g., "June 1944", "morning of D-Day")
- role_in_event: Describe the place's role in this event (e.g., "target of attack", "defensive position", "supply route")
- Use null if not mentioned in text

Generate 26-character ULIDs using only: 0-9 A-H J-K M-N P-T V-Z
All places MUST have latitude/longitude coordinates.
If no places found, return empty Place_Mentions array.

IMPORTANT: Do NOT extract military units, divisions, corps, armies, or organizations as places.
Entries beginning with numbers (1st, 2nd, 3rd), Roman numerals (I, II, III, IV, V, VI, VII),
or number words (First, Second, Third) are military units and belong in people_groups, not places.
Examples of what to EXCLUDE: "1st Infantry Division", "VII Corps", "Third Army", "12th Army Group headquarters"."""

    try:
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "places",
            event_name=event_name,
            event_id=event_id,
            sub_event_summary=sub_event_summary,
            sub_event_id=sub_event_id,
            text=text,
        )
    except Exception as e:
        logger.warning("Place extraction step failed: %s", e)

    return prompt


def _load_places_index(places_dir: Path) -> dict:
    """Load existing places index."""
    index_file = places_dir / "index.json"

    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    return {}


def _load_book_metadata(parsed_file: Optional[Path]) -> tuple[str, str, str]:
    """Load book metadata from parsed file. Returns (book, author, series)."""
    if not parsed_file or not parsed_file.exists():
        return "", "", ""

    with open(parsed_file, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)
        return (
            parsed_data.get("book", ""),
            parsed_data.get("author", ""),
            parsed_data.get("series", ""),
        )


def _extract_place_for_sub_event(
    sub_event: Dict[str, Any],
    event_id: str,
    event_name: str,
    grok_client: GrokClient,
    max_retries: int,
) -> Optional[Dict[str, Any]]:
    """Extract places for a single sub-event with retry logic."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    logger.info("  Processing sub-event %s", sub_event_id)

    prompt = create_place_prompt(sub_event, event_id, event_name)

    for attempt in range(max_retries):
        try:
            place_output = grok_client.extract_structured(
                prompt=prompt,
                schema=PlaceOutput,
                system_prompt=SYSTEM_PROMPT,
                use_cache=(attempt == 0),
                cache_type="places",
            )

            place_dict: Dict[str, Any] = place_output.model_dump(by_alias=True)
            place_dict = _fix_null_fields(place_dict)  # type: ignore
            place_dict = _fix_invalid_ulids(place_dict)  # type: ignore

            return place_dict

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning("    Attempt %d failed: %s. Retrying...", attempt + 1, e)
            else:
                logger.error(
                    "    All %d attempts failed for %s", max_retries, sub_event_id
                )
                raise

    return None


def extract_places(
    event_file: Path,
    grok_client: GrokClient,
    places_dir: Path,
    parsed_file: Optional[Path] = None,
    max_retries: int = 3,
) -> Optional[Path]:
    """
    Extract places from event file and add to central repository.

    Args:
        event_file: Path to event JSON file
        grok_client: Grok API client
        places_dir: Central places directory (output/places/)
        parsed_file: Path to parsed JSON file (for book metadata)
        max_retries: Maximum retry attempts per sub-event

    Returns:
        Path to places directory, or None if failed
    """
    places_dir.mkdir(parents=True, exist_ok=True)
    index = _load_places_index(places_dir)

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    # Get book metadata
    book, author, series = _load_book_metadata(parsed_file)

    # Validate required metadata (series is optional)
    if not book or not author:
        raise ValueError(
            f"Missing required book metadata in {parsed_file}: "
            f"book={book!r}, author={author!r}"
        )

    event_name = event_data.get("Chapter", "")
    event_obj = event_data.get("Event", {})
    event_id = event_obj.get("EventID", "")
    sub_events = event_obj.get("Sub-events", [])

    places_updated = 0

    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_name = sub_event.get("Sub-event_summary", "")

        place_dict = _extract_place_for_sub_event(
            sub_event, event_id, event_name, grok_client, max_retries
        )

        if not place_dict:
            continue

        # Process each place mention
        for mention in place_dict.get("Place_Mentions", []):
            place_name = mention.get("current_name")
            if not place_name:
                continue

            # Find or create place file
            place_file = _find_or_create_place(place_name, mention, places_dir, index)

            # Add event mention to place file
            _add_event_mention(
                place_file,
                mention,
                event_name,
                event_id,
                sub_event_name,
                sub_event_id,
                book,
                author,
                series,
            )

            places_updated += 1

        logger.info(
            "  ✓ Processed %d place mentions",
            len(place_dict.get("Place_Mentions", [])),
        )

    # Save updated index
    index_file = places_dir / "index.json"
    write_json_with_lock(index_file, index)

    logger.info(f"  Updated {places_updated} place(s) in central repository")
    return places_dir if places_updated > 0 else None


def _find_or_create_place(
    place_name: str,
    mention: Dict[str, Any],
    places_dir: Path,
    index: Dict[str, str],
) -> Path:
    """Find existing place file or create new one."""
    place_key = place_name.lower()

    if place_key in index:
        # Existing place
        return places_dir / index[place_key]

    # New place - create file
    place_id = mention.get("PlaceMentionID", str(ulid.new()))
    safe_name = place_name.replace(" ", "_").replace("/", "_")
    filename = f"{safe_name}_{place_id[:8]}.json"

    place_file = places_dir / filename

    # Initialize place data
    place_data = {
        "PlaceID": place_id,
        "current_name": place_name,
        "historical_names": [],
        "aliases": [],
        "source_language": mention.get("source_language", "English"),
        "geography_type": mention.get("geography_type", "other"),
        "event_mentions": [],
    }

    # Add coordinates if present
    if mention.get("latitude") and mention.get("longitude"):
        lat = mention["latitude"]
        lon = mention["longitude"]
        place_data["coordinates"] = {
            "latitude": lat,
            "longitude": lon,
            "precision": "approximate",
            "confidence": 0.8,
        }
        place_data["bounding_box"] = mention.get("bounding_box")
        # Generate map URLs if not present
        place_data["map_urls"] = mention.get("map_urls") or _generate_map_urls(lat, lon)

    # Write initial file
    write_json_with_lock(place_file, place_data)

    # Update index
    index[place_key] = filename

    logger.debug(f"  Created new place: {filename}")
    return place_file


def _build_place_name_index(
    places_dir: Path,
) -> tuple[Dict[str, str], Dict[Path, Dict[str, Any]]]:
    """Build lowercase name → PlaceID index and load all place data."""
    skip = {"index.json", "not_duplicates.json"}
    name_to_id: Dict[str, str] = {}
    file_data: Dict[Path, Dict[str, Any]] = {}
    for pf in places_dir.glob("*.json"):
        if pf.name in skip:
            continue
        try:
            with open(pf, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or not data.get("PlaceID"):
                continue
            file_data[pf] = data
            name = (data.get("current_name") or "").lower()
            if name:
                name_to_id[name] = data["PlaceID"]
            for alias in data.get("aliases", []):
                name_to_id[alias.lower()] = data["PlaceID"]
        except (OSError, json.JSONDecodeError):
            continue
    return name_to_id, file_data


def _find_parent_id(
    hierarchy: Dict[str, Any], name_to_id: Dict[str, str], own_id: str = ""
) -> Optional[str]:
    """Find parent PlaceID from hierarchy region or country. Skips self."""
    for field in ("region", "country", "continent"):
        parent_name = hierarchy.get(field, "")
        if parent_name:
            parent_id = name_to_id.get(parent_name.lower())
            if parent_id and parent_id != own_id:
                return parent_id
    return None


def link_parent_place_ids(places_dir: Path) -> int:
    """Link parent_place_id for all places using hierarchy data.

    Processes in order: countries → regions → cities/towns/other.
    Returns number of places updated.
    """
    if not places_dir.exists():
        return 0

    name_to_id, file_data = _build_place_name_index(places_dir)

    # Process in hierarchy order so parents are indexed before children
    priority = {"continent": 0, "country": 1, "region": 2}
    ordered = sorted(
        file_data.items(),
        key=lambda item: priority.get(item[1].get("geography_type", ""), 3),
    )

    updated = 0
    for pf, data in ordered:
        hierarchy = data.get("hierarchy")
        if not hierarchy or not isinstance(hierarchy, dict):
            continue
        if hierarchy.get("parent_place_id"):
            continue
        parent_id = _find_parent_id(hierarchy, name_to_id, data.get("PlaceID", ""))
        if parent_id:
            hierarchy["parent_place_id"] = parent_id
            write_json_with_lock(pf, data)
            updated += 1

    logger.info("Linked parent_place_id for %d places", updated)
    return updated


def _backfill_place_data(place_data: Dict[str, Any], mention: Dict[str, Any]) -> None:
    """Backfill missing v3 schema fields on legacy place files."""
    # Rename 'name' → 'current_name' if needed
    if "name" in place_data and "current_name" not in place_data:
        place_data["current_name"] = place_data.pop("name")

    place_data.setdefault("historical_names", [])
    place_data.setdefault("aliases", [])
    place_data.setdefault("source_language", mention.get("source_language", "English"))

    if not place_data.get("geography_type"):
        place_data["geography_type"] = mention.get("geography_type", "other")

    # Add coordinates if missing
    lat = mention.get("latitude", 0)
    lon = mention.get("longitude", 0)
    if lat and lon and "coordinates" not in place_data:
        place_data["coordinates"] = {
            "latitude": lat,
            "longitude": lon,
            "precision": "approximate",
            "confidence": 0.8,
        }
        place_data["bounding_box"] = _calculate_bounding_box(lat, lon)
        place_data["map_urls"] = _generate_map_urls(lat, lon)

    # Add historical name if provided and not already present
    hist_name = mention.get("historical_name")
    if hist_name:
        existing = {h["name"] for h in place_data.get("historical_names", [])}
        if hist_name not in existing:
            place_data["historical_names"].append(
                {
                    "name": hist_name,
                    "language": mention.get("source_language", "English"),
                }
            )


def _add_event_mention(
    place_file: Path,
    mention: Dict[str, Any],
    event_name: str,
    event_id: str,
    sub_event_name: str,
    sub_event_id: str,
    book: str,
    author: str,
    series: str,
) -> None:
    """Add event mention to place file."""
    from src.utils.file_lock import locked_json

    with locked_json(place_file) as (place_data, save):
        # Backfill missing v3 fields from mention data
        _backfill_place_data(place_data, mention)

        # Create event mention
        event_mention = {
            "MentionID": str(ulid.new()),
            "Event_Name": event_name,
            "EventID": event_id,
            "Sub_event_Name": sub_event_name,
            "Sub_eventID": sub_event_id,
            "book": book,
            "author": author,
            "series": series,
            "date_context": mention.get("date_context"),
            "DateMentionID": None,  # TODO: Link to dates extraction
            "role_in_event": mention.get("role_in_event"),
            "original_text": mention.get("original_text", ""),
        }

        # Check for duplicate mention (same sub-event)
        existing = [
            m
            for m in place_data.get("event_mentions", [])
            if m["Sub_eventID"] == sub_event_id
        ]

        if not existing:
            place_data.setdefault("event_mentions", []).append(event_mention)
            save(place_data)
            logger.debug(f"  Added mention to {place_file.name}")
