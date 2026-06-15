"""Weather extraction with central repository and API integration."""

import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import ulid
from pydantic import BaseModel, ConfigDict, Field

from src.grok_client import GrokClient
from src.utils.file_lock import write_json_with_lock
from src.utils.http_pool import get_session
from src.utils.json_validator import _fix_invalid_ulids

from src.utils.prompt_loader import get_system_prompt

logger = logging.getLogger(__name__)


class WeatherMention(BaseModel):
    """Weather mention from document."""

    WeatherMentionID: str = Field(description="26-character ULID")
    place_name: str = Field(description="Place name")
    PlaceMentionID: Optional[str] = Field(default=None, description="Link to place")
    date: str = Field(description="ISO date YYYY-MM-DD")
    DateMentionID: Optional[str] = Field(default=None, description="Link to date")
    weather_description: str = Field(description="Weather description")
    temperature: Optional[float] = Field(default=None, description="Temperature value")
    temperature_unit: Optional[str] = Field(
        default=None, description="celsius or fahrenheit"
    )
    measurement_system: Optional[str] = Field(
        default=None, description="metric or imperial"
    )
    notable_impact: Optional[str] = Field(
        default=None, description="Impact on operations"
    )
    original_text: str = Field(description="Exact text from document")


class WeatherOutput(BaseModel):
    """Weather extraction output."""

    Event_Name: str = Field(description="Event name")
    EventID: str = Field(description="26-character ULID")
    Sub_event_Name: str = Field(description="Sub-event name", alias="Sub-event_Name")
    Sub_eventID: str = Field(description="26-character ULID", alias="Sub-eventID")
    Weather_Mentions: List[WeatherMention] = Field(description="Weather mentions")

    model_config = ConfigDict(populate_by_name=True)


def _is_valid_weather_mention(mention: dict) -> tuple[bool, str]:
    """Check if weather mention is valid. Returns (is_valid, reason)."""
    if not isinstance(mention, dict):
        return False, "not a dict"

    if not mention.get("date"):
        return False, f"null date: {mention.get('original_text', 'unknown')}"

    # Check for exact date (YYYY-MM-DD format)
    date_str = mention.get("date", "")
    if not date_str or len(date_str) != 10 or date_str.count("-") != 2:
        return False, f"approximate date: {date_str}"

    if not mention.get("weather_description"):
        return False, "null description"

    if not mention.get("original_text"):
        return False, "null original_text"

    return True, ""


def _filter_invalid_weather(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove weather mentions with missing required fields or approximate dates."""
    if "Weather_Mentions" in data and isinstance(data["Weather_Mentions"], list):
        original_count = len(data["Weather_Mentions"])
        valid_weather = []

        for mention in data["Weather_Mentions"]:
            is_valid, reason = _is_valid_weather_mention(mention)
            if is_valid:
                valid_weather.append(mention)
            else:
                logger.warning("  Filtered weather mention with %s", reason)

        filtered_count = original_count - len(valid_weather)
        if filtered_count > 0:
            logger.info("  Filtered %d invalid weather mention(s)", filtered_count)

        data["Weather_Mentions"] = valid_weather
    return data


def _fetch_weather_from_api(
    date: str, lat: float, lon: float, timeout: int = 30
) -> Optional[Dict[str, Any]]:
    """Fetch historical weather from Open-Meteo API."""
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "start_date": date,
        "end_date": date,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,cloud_cover_mean",
    }

    try:
        session = get_session()
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.warning("  Failed to fetch weather from API: %s", e)
        return None


@lru_cache(maxsize=5000)
def _normalize_weather_key(date: str, place_name: str) -> str:
    """Create normalized key for weather lookup."""
    return f"{date}_{place_name.replace(' ', '_')}"


def _build_date_id_lookup(dates_dir: Path) -> Dict[str, str]:
    """Build date_start → DateID map from dates directory."""
    lookup: Dict[str, str] = {}
    if not dates_dir.exists():
        return lookup
    for f in dates_dir.glob("*.json"):
        if f.name == "index.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ds = data.get("date_start")
        did = data.get("DateID")
        if ds and did:
            lookup[ds] = did
    return lookup


def _lookup_by_place_id(
    place_id: str, places_dir: Path, places_index: dict
) -> tuple[float, float, Optional[str], Optional[str]]:
    """Look up coordinates by PlaceID. Returns (lat, lon, place_id, country)."""
    for place_file_name in places_index.values():
        place_file = places_dir / place_file_name
        if place_file.exists():
            with open(place_file, "r", encoding="utf-8") as f:
                place_data = json.load(f)

            if place_data.get("PlaceID") == place_id:
                coords = place_data.get("coordinates", {})
                latitude = coords.get("latitude", 0.0)
                longitude = coords.get("longitude", 0.0)
                country = place_data.get("country")
                logger.info("    Found coordinates via PlaceID: %s", place_id[:8])
                return latitude, longitude, place_id, country

    return 0.0, 0.0, place_id, None


def _lookup_by_name_fuzzy(
    place_name: str, places_dir: Path, places_index: dict
) -> tuple[float, float, Optional[str], Optional[str]]:
    """Look up coordinates by fuzzy name match. Returns (lat, lon, place_id, country)."""
    place_name_lower = place_name.lower()

    for place_key, place_file_name in places_index.items():
        if place_name_lower in place_key.lower():
            place_file = places_dir / place_file_name
            if place_file.exists():
                with open(place_file, "r", encoding="utf-8") as f:
                    place_data = json.load(f)

                coords = place_data.get("coordinates", {})
                latitude = coords.get("latitude", 0.0)
                longitude = coords.get("longitude", 0.0)
                if latitude != 0.0 and longitude != 0.0:
                    found_place_id = place_data.get("PlaceID")
                    logger.info(
                        "    Found coordinates via fuzzy match: %s -> %s",
                        place_name,
                        place_key,
                    )
                    return latitude, longitude, found_place_id, None

    return 0.0, 0.0, None, None


def _lookup_coordinates(
    place_id: Optional[str], place_name: str, places_dir: Path
) -> tuple[float, float, Optional[str], Optional[str]]:
    """
    Look up coordinates and country from places repository.

    Returns:
        (latitude, longitude, place_id, country) tuple
    """
    if not places_dir.exists():
        return 0.0, 0.0, place_id, None

    places_index_file = places_dir / "index.json"
    if not places_index_file.exists():
        return 0.0, 0.0, place_id, None

    with open(places_index_file, "r", encoding="utf-8") as f:
        places_index = json.load(f)

    # Option 1: Look up by PlaceID if provided
    if place_id:
        lat, lon, pid, country = _lookup_by_place_id(place_id, places_dir, places_index)
        if lat != 0.0 and lon != 0.0:
            return lat, lon, pid, country

    # Option 2: Fallback to fuzzy match by name
    return _lookup_by_name_fuzzy(place_name, places_dir, places_index)


def _create_api_data_dict(api_response: dict) -> dict:
    """Create API data dictionary from response."""
    daily_data = api_response.get("daily", {})
    return {
        "provider": "open-meteo",
        "data_type": "reanalysis",
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "temperature_max_c": daily_data.get("temperature_2m_max", [None])[0],
        "temperature_min_c": daily_data.get("temperature_2m_min", [None])[0],
        "precipitation_mm": daily_data.get("precipitation_sum", [None])[0],
        "windspeed_max_kmh": daily_data.get("windspeed_10m_max", [None])[0],
        "cloud_cover_percent": daily_data.get("cloud_cover_mean", [None])[0],
        "raw_response": api_response,
    }


def _update_existing_weather(
    weather_file: Path,
    weather_data: dict,
    mention: Dict[str, Any],
    place_name: str,
    places_dir: Path,
    fetch_api: bool,
    date: str,
) -> bool:
    """Update existing weather file if needed. Returns True if updated."""
    updated_coords = _maybe_update_coordinates(
        weather_data, mention, place_name, places_dir, weather_file
    )
    updated_api = _maybe_fetch_api_data(weather_data, fetch_api, date, weather_file)

    if updated_coords or updated_api:
        write_json_with_lock(weather_file, weather_data)
        return True
    return False


def _maybe_update_coordinates(
    weather_data: dict,
    mention: Dict[str, Any],
    place_name: str,
    places_dir: Path,
    weather_file: Path,
) -> bool:
    """Update coordinates if missing. Returns True if updated."""
    loc = weather_data["location"]
    if loc["latitude"] != 0.0 or loc["longitude"] != 0.0:
        return False
    latitude, longitude, place_id, _country = _lookup_coordinates(
        mention.get("PlaceMentionID"), place_name, places_dir
    )
    if latitude == 0.0 and longitude == 0.0:
        return False
    loc["latitude"] = latitude
    loc["longitude"] = longitude
    loc["PlaceID"] = place_id
    logger.info("    Updated coordinates for %s", weather_file.name)
    return True


def _maybe_fetch_api_data(
    weather_data: dict, fetch_api: bool, date: str, weather_file: Path
) -> bool:
    """Fetch API data if enabled and missing. Returns True if updated."""
    if not fetch_api or weather_data.get("api_data") is not None:
        return False
    lat = weather_data["location"]["latitude"]
    lon = weather_data["location"]["longitude"]
    if lat == 0.0 and lon == 0.0:
        return False
    api_response = _fetch_weather_from_api(date, lat, lon)
    if not api_response:
        return False
    weather_data["source_type"] = "hybrid"
    weather_data["api_data"] = _create_api_data_dict(api_response)
    logger.info("    Added API data to %s", weather_file.name)
    return True


def _create_new_weather_file(
    mention: Dict[str, Any],
    date: str,
    place_name: str,
    weather_dir: Path,
    places_dir: Path,
    fetch_api: bool,
    date_id_lookup: Optional[Dict[str, str]] = None,
) -> tuple[Path, str]:
    """Create new weather file. Returns (weather_file, filename)."""
    weather_id = str(ulid.new())
    safe_date = date.replace("-", "")
    safe_place = place_name.replace(" ", "_").replace(",", "")
    filename = f"{safe_date}_{safe_place}_{weather_id[:8]}.json"
    weather_file = weather_dir / filename

    # Look up coordinates
    latitude, longitude, place_id, _country = _lookup_coordinates(
        mention.get("PlaceMentionID"), place_name, places_dir
    )

    # Initialize weather data
    weather_data: Dict[str, Any] = {
        "WeatherID": weather_id,
        "date": date,
        "DateID": (date_id_lookup or {}).get(date),
        "location": {
            "place_name": place_name,
            "PlaceID": place_id,
            "latitude": latitude,
            "longitude": longitude,
        },
        "source_type": "extracted",
        "extracted_data": None,
        "api_data": None,
        "event_mentions": [],
    }

    # Fetch API data if enabled and coordinates available
    if fetch_api and latitude != 0.0 and longitude != 0.0:
        api_response = _fetch_weather_from_api(date, latitude, longitude)
        if api_response:
            weather_data["source_type"] = "hybrid"
            weather_data["api_data"] = _create_api_data_dict(api_response)

    write_json_with_lock(weather_file, weather_data)
    logger.info("    Created weather file: %s", filename)

    return weather_file, filename


def _find_or_create_weather(
    mention: Dict[str, Any],
    weather_dir: Path,
    index: Dict[str, str],
    places_dir: Path,
    fetch_api: bool = True,
    date_id_lookup: Optional[Dict[str, str]] = None,
) -> Path:
    """Find existing weather file or create new one, updating if needed."""
    date = mention.get("date", "")
    place_name = mention.get("place_name", "")

    # Create lookup key
    weather_key = _normalize_weather_key(date, place_name)

    # Check if file exists
    if weather_key in index:
        weather_file = weather_dir / index[weather_key]

        # Load existing file
        with open(weather_file, "r", encoding="utf-8") as f:
            weather_data = json.load(f)

        # Update if needed
        _update_existing_weather(
            weather_file, weather_data, mention, place_name, places_dir, fetch_api, date
        )

        return weather_file

    # Create new weather file
    weather_file, filename = _create_new_weather_file(
        mention, date, place_name, weather_dir, places_dir, fetch_api, date_id_lookup
    )

    index[weather_key] = filename
    return weather_file


def _add_event_mention(
    weather_file: Path,
    mention: Dict[str, Any],
    event_name: str,
    event_id: str,
    sub_event_name: str,
    sub_event_id: str,
    book: str,
    author: str,
    series: str,
) -> None:
    """Add event mention and extracted data to weather file."""
    from src.utils.file_lock import locked_json

    with locked_json(weather_file) as (weather_data, save):
        # Check for duplicate mention
        existing = [
            m
            for m in weather_data.get("event_mentions", [])
            if m["Sub_eventID"] == sub_event_id
        ]
        if existing:
            logger.info("    Weather already has mention from this sub-event, skipping")
            return

        # Add extracted data if not present
        if not weather_data.get("extracted_data"):
            weather_data["extracted_data"] = {
                "description": mention.get("weather_description", ""),
                "temperature": mention.get("temperature") or None,
                "temperature_unit": mention.get("temperature_unit") or None,
                "measurement_system": mention.get("measurement_system") or None,
                "notable_impact": mention.get("notable_impact") or None,
                "original_text": mention.get("original_text", ""),
                "book": book,
                "author": author,
            }
            if weather_data.get("api_data"):
                weather_data["source_type"] = "hybrid"

        # Add event mention
        event_mention = {
            "MentionID": str(ulid.new()),
            "Event_Name": event_name,
            "EventID": event_id,
            "Sub_event_Name": sub_event_name,
            "Sub_eventID": sub_event_id,
            "book": book,
            "author": author,
            "series": series,
        }
        weather_data.setdefault("event_mentions", []).append(event_mention)
        save(weather_data)

    logger.info("    Added mention to %s", weather_file.name)


def _build_places_section(
    sub_event: Dict[str, Any], places_index: Optional[Dict[str, str]] = None
) -> str:
    """Build places section for prompt using the places index."""
    if not places_index:
        return ""

    # Match place names from the sub-event text against the index
    text = ""
    fulltext = sub_event.get("Sub-event_fulltext", {})
    if fulltext:
        text = " ".join(str(v) for v in fulltext.values()).lower()
    summary = sub_event.get("Sub-event_summary", "").lower()
    combined = text + " " + summary

    if not combined.strip():
        return ""

    # Find places mentioned in this sub-event's text
    matched = []
    for name, place_id in places_index.items():
        if name.lower() in combined:
            matched.append(f"  - {name}: {place_id}")

    if matched:
        return (
            "\n\nAvailable Places (COPY these IDs exactly — do NOT generate new ones):\n"
            + "\n".join(matched[:20])
        )
    return ""


def _build_dates_section(sub_event: Dict[str, Any]) -> str:
    """Build dates section for prompt."""
    if "Dates" not in sub_event or not sub_event["Dates"]:
        return ""

    dates_list = [
        f"  - {d.get('date_start', 'Unknown')}: {d.get('DateMentionID', 'N/A')}"
        for d in sub_event["Dates"]
        if isinstance(d, dict)
    ]

    if dates_list:
        return "\n\nAvailable Dates (link to these):\n" + "\n".join(dates_list)

    return ""


def create_weather_prompt(
    sub_event: Dict[str, Any],
    event_id: str,
    event_name: str,
    places_index: Optional[Dict[str, str]] = None,
) -> str:
    """Create prompt for weather extraction."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    sub_event_summary = sub_event.get("Sub-event_summary", "")
    fulltext = sub_event.get("Sub-event_fulltext", {})

    text = "\n".join(fulltext.values())
    if not text.strip() and not sub_event_summary.strip():
        logger.debug("Skipping empty sub-event %s (weather)", sub_event_id)
        return ""

    # Extract available PlaceIDs and DateIDs
    places_section = _build_places_section(sub_event, places_index)
    dates_section = _build_dates_section(sub_event)

    prompt = f"""Extract weather mentions from this WWII event text.

Event: {event_name}
EventID: {event_id}
Sub-event: {sub_event_summary}
Sub-eventID: {sub_event_id}{places_section}{dates_section}

Text:
{text}

Return JSON matching this structure:
{{
  "Event_Name": "{event_name}",
  "EventID": "{event_id}",
  "Sub_event_Name": "{sub_event_summary}",
  "Sub_eventID": "{sub_event_id}",
  "Weather_Mentions": [
    {{
      "WeatherMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
      "place_name": "Normandy",
      "PlaceMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
      "date": "1944-06-06",
      "DateMentionID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
      "weather_description": "Clear skies with light fog",
      "temperature": 15,
      "temperature_unit": "celsius",
      "measurement_system": "metric",
      "notable_impact": "Fog delayed H-Hour",
      "original_text": "The morning fog lifted by 0600 hours"
    }}
  ]
}}

IMPORTANT:
- Link PlaceMentionID and DateMentionID from the available lists above
- Only extract EXACT dates (YYYY-MM-DD). Skip "early June", "mid-summer", etc.
- Generate 26-character ULIDs using: 0-9 A-H J-K M-N P-T V-Z"""

    try:
        from src.utils.prompt_loader import get_system_prompt, render_prompt

        prompt = render_prompt(
            "weather",
            event_name=event_name,
            event_id=event_id,
            sub_event_summary=sub_event_summary,
            sub_event_id=sub_event_id,
            places_section=places_section,
            dates_section=dates_section,
            text=text,
        )
    except Exception as e:
        logger.warning("Weather extraction step failed: %s", e)
    return prompt


def _load_weather_index(weather_dir: Path) -> dict:
    """Load weather index from file."""
    index_file = weather_dir / "index.json"

    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)

    return {}


def _load_book_metadata_for_weather(
    parsed_file: Optional[Path],
) -> tuple[str, str, str]:
    """Load book metadata from parsed file. Returns (book, author, series)."""
    if not parsed_file or not parsed_file.exists():
        return "", "", ""

    with open(parsed_file, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)
        if not isinstance(parsed_data, dict):
            return "", "", ""

        return (
            parsed_data.get("book", ""),
            parsed_data.get("author", ""),
            parsed_data.get("series", ""),
        )


def _batch_extract_weather(
    sub_events: list,
    event_id: str,
    event_name: str,
    grok_client: GrokClient,
    max_retries: int,
    places_index: Optional[Dict[str, str]] = None,
    chunk_size: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """Extract weather from sub-events, chunked for large chapters.

    Returns dict mapping sub_event_id → list of weather mention dicts.
    """
    if not sub_events:
        return {}

    # Build per-sub-event blocks with their place/date context
    blocks = []
    for se in sub_events:
        seid = se.get("Sub-eventID", "")
        summary = se.get("Sub-event_summary", "")
        fulltext = se.get("Sub-event_fulltext", {})
        text = "\n".join(fulltext.values()) if fulltext else ""
        if not text:
            continue
        places_section = _build_places_section(se, places_index)
        dates_section = _build_dates_section(se)
        blocks.append(
            f"--- Sub-event [{seid}] {summary} ---{places_section}{dates_section}\n\nText:\n{text}"
        )

    if not blocks:
        return {}

    # Chunk to avoid truncation on large chapters
    chunks = [blocks[i : i + chunk_size] for i in range(0, len(blocks), chunk_size)]
    all_results: Dict[str, List[Dict[str, Any]]] = {}

    from src.utils.chunked_extract import extract_with_chunk_halving

    def _extract_weather_chunk(chunk):
        from src.utils.prompt_loader import get_system_prompt, render_prompt as _rp

        prompt = _rp(
            "weather_batch",
            event_name=event_name,
            event_id=event_id,
            sub_event_blocks="".join(chr(10) + chr(10) + b for b in chunk),
        )
        return _call_and_parse_weather(grok_client, prompt, max_retries)

    return extract_with_chunk_halving(chunks, _extract_weather_chunk, "weather")


def _call_and_parse_weather(
    grok_client: GrokClient, prompt: str, max_retries: int
) -> Dict[str, List[Dict[str, Any]]]:
    """Call Grok API with retries and parse batched weather response."""
    for attempt in range(max_retries):
        try:
            response = grok_client.extract_json(
                prompt=prompt,
                system_prompt=get_system_prompt("weather"),
                use_cache=(attempt == 0),
                cache_type="weather",
            )
            if isinstance(response, dict):
                return _parse_weather_response(response)
            return {}
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    "  ⚠ Batch weather attempt %d failed: %s", attempt + 1, e
                )
            else:
                import os

                if os.environ.get("PIPELINE_PHASE"):
                    logger.info("  ⊘ Sync weather fallback skipped (batch mode): %s", e)
                else:
                    logger.error("  ✗ All %d batch attempts failed: %s", max_retries, e)
    return {}


def _parse_weather_response(
    response: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Validate and fix ULIDs in batched weather response."""
    result = {}
    for seid, mentions in response.items():
        if not isinstance(mentions, list):
            continue
        fixed = _fix_invalid_ulids(mentions)
        if isinstance(fixed, list):
            mentions = fixed
        result[seid] = [
            m
            for m in mentions
            if isinstance(m, dict) and m.get("date") and m.get("place_name")
        ]
    return result


def extract_weather_central(
    event_file: Path,
    weather_dir: Path,
    grok_client: GrokClient,
    places_dir: Path,
    parsed_file: Optional[Path] = None,
    fetch_api: bool = False,
    max_retries: int = 3,
) -> Optional[Path]:
    """
    Extract weather from event file and add to central repository.

    Args:
        event_file: Path to event JSON file
        weather_dir: Central weather directory
        grok_client: Grok API client
        places_dir: Central places directory (for coordinate lookup)
        parsed_file: Path to parsed JSON (for book metadata)
        fetch_api: Whether to fetch API data
        max_retries: Maximum retry attempts per sub-event

    Returns:
        Path to weather directory, or None if failed
    """
    weather_dir.mkdir(parents=True, exist_ok=True)

    # Load index
    index = _load_weather_index(weather_dir)

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    # Validate event_data
    if not isinstance(event_data, dict):
        logger.error(
            "Invalid event data format in %s: expected dict, got %s",
            event_file,
            type(event_data).__name__,
        )
        return None

    # Get book metadata
    book, author, series = _load_book_metadata_for_weather(parsed_file)

    if not book or not author:
        raise ValueError(
            f"Missing required book metadata: book={book!r}, author={author!r}"
        )

    event_name = event_data.get("Chapter", "")
    event_obj = event_data.get("Event", {})
    event_id = event_obj.get("EventID", "")
    sub_events = event_obj.get("Sub-events", [])

    weather_updated = 0

    # Build date string → DateID lookup for resolving LLM-generated refs
    dates_dir = places_dir.parent / "dates"
    date_id_lookup = _build_date_id_lookup(dates_dir)

    # Build places name→ID index for cross-referencing in prompts
    places_name_index: Dict[str, str] = {}
    places_index_file = places_dir / "index.json"
    if places_index_file.exists():
        try:
            raw = json.loads(places_index_file.read_text(encoding="utf-8"))
            # index.json maps name → filename; we need name → PlaceID
            for name, filename in raw.items():
                place_file = places_dir / filename
                if place_file.exists():
                    try:
                        pd = json.loads(place_file.read_text(encoding="utf-8"))
                        pid = pd.get("PlaceID", "")
                        if pid:
                            places_name_index[name] = pid
                    except (json.JSONDecodeError, OSError):
                        pass
        except (json.JSONDecodeError, OSError):
            pass

    # Batch extract from all sub-events in single API call
    batch_results = _batch_extract_weather(
        sub_events, event_id, event_name, grok_client, max_retries, places_name_index
    )

    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_name = sub_event.get("Sub-event_summary", "")
        mentions = batch_results.get(sub_event_id, [])

        for mention in mentions:
            weather_file = _find_or_create_weather(
                mention, weather_dir, index, places_dir, fetch_api, date_id_lookup
            )
            _add_event_mention(
                weather_file,
                mention,
                event_name,
                event_id,
                sub_event_name,
                sub_event_id,
                book,
                author,
                series,
            )
            weather_updated += 1

    # Save index
    index_file = weather_dir / "index.json"
    write_json_with_lock(index_file, index)

    logger.info("Updated %d weather mentions in central repository", weather_updated)
    return weather_dir if weather_updated > 0 else None
