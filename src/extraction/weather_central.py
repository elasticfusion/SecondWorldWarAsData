"""Weather extraction with central repository and API integration."""

import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests
import ulid
from pydantic import BaseModel, ConfigDict, Field

from src.grok_client import GrokClient
from src.utils.file_lock import write_json_with_lock
from src.utils.http_pool import get_session

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


SYSTEM_PROMPT = """You are an expert historian analyzing World War II documents.
Extract weather mentions from the provided event text.

CRITICAL RULES:
1. ONLY extract weather explicitly mentioned in the text.
2. ONLY extract for EXACT dates (e.g., "June 6, 1944"). Skip approximate dates like "early June".
3. ALWAYS link to PlaceMentionID when the place is mentioned in the text.
4. ALWAYS link to DateMentionID when the date is mentioned in the text.
5. Extract temperature with units (celsius/fahrenheit).
6. Note notable operational impacts.
7. Generate valid 26-character ULIDs for new mentions.
8. Return complete, valid JSON.

IMPORTANT: Cross-reference places and dates to link weather to existing entities.

If no weather mentions found, return empty Weather_Mentions array."""


def _fix_invalid_ulids(
    data: Union[Dict[str, Any], List[Any]],
) -> Union[Dict[str, Any], List[Any]]:
    """Replace invalid ULIDs with valid ones."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("ID") and isinstance(value, str):
                if len(value) != 26 or not all(
                    c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in value
                ):
                    data[key] = str(ulid.new())
            elif isinstance(value, (dict, list)):
                data[key] = _fix_invalid_ulids(value)
    elif isinstance(data, list):
        return [_fix_invalid_ulids(item) for item in data]
    return data


def _filter_invalid_weather(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove weather mentions with missing required fields or approximate dates."""
    if "Weather_Mentions" in data and isinstance(data["Weather_Mentions"], list):
        original_count = len(data["Weather_Mentions"])
        valid_weather = []

        for mention in data["Weather_Mentions"]:
            if not isinstance(mention, dict):
                continue

            # Check required fields
            if not mention.get("date"):
                logger.warning(
                    "  Filtered weather mention with null date: %s",
                    mention.get("original_text", "unknown"),
                )
                continue

            # Check for exact date (YYYY-MM-DD format)
            date_str = mention.get("date", "")
            if not date_str or len(date_str) != 10 or date_str.count("-") != 2:
                logger.warning(
                    "  Filtered weather mention with approximate date: %s", date_str
                )
                continue

            if not mention.get("weather_description"):
                logger.warning("  Filtered weather mention with null description")
                continue

            if not mention.get("original_text"):
                logger.warning("  Filtered weather mention with null original_text")
                continue

            valid_weather.append(mention)

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


def _lookup_coordinates(
    place_id: Optional[str], place_name: str, places_dir: Path
) -> tuple[float, float, Optional[str], Optional[str]]:
    """
    Look up coordinates and country from places repository.

    Returns:
        (latitude, longitude, place_id, country) tuple
    """
    latitude = 0.0
    longitude = 0.0
    found_place_id = place_id
    country = None

    if not places_dir.exists():
        return latitude, longitude, found_place_id, country

    places_index_file = places_dir / "index.json"
    if not places_index_file.exists():
        return latitude, longitude, found_place_id, country

    with open(places_index_file, "r", encoding="utf-8") as f:
        places_index = json.load(f)

    # Option 1: Look up by PlaceID if provided
    if place_id:
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
                    return latitude, longitude, found_place_id, country

    # Option 2: Fallback to fuzzy match by name
    if latitude == 0.0 and longitude == 0.0:
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
                        return latitude, longitude, found_place_id, country

    return latitude, longitude, found_place_id, country


def _find_or_create_weather(
    mention: Dict[str, Any],
    weather_dir: Path,
    index: Dict[str, str],
    places_dir: Path,
    fetch_api: bool = True,
) -> Path:
    """Find existing weather file or create new one, updating if needed."""
    date = mention.get("date", "")
    place_name = mention.get("place_name", "")

    # Create lookup key
    weather_key = _normalize_weather_key(date, place_name)

    # Check if file exists
    if weather_key in index:
        weather_file = weather_dir / index[weather_key]

        # Load existing file to check if update needed
        with open(weather_file, "r", encoding="utf-8") as f:
            weather_data = json.load(f)

        needs_update = False

        # Update coordinates if missing
        if (
            weather_data["location"]["latitude"] == 0.0
            and weather_data["location"]["longitude"] == 0.0
        ):
            latitude, longitude, place_id, country = _lookup_coordinates(
                mention.get("PlaceMentionID"), place_name, places_dir
            )
            if latitude != 0.0 and longitude != 0.0:
                weather_data["location"]["latitude"] = latitude
                weather_data["location"]["longitude"] = longitude
                weather_data["location"]["PlaceID"] = place_id
                weather_data["location"]["country"] = country
                needs_update = True
                logger.info("    Updated coordinates for %s", weather_file.name)

        # Fetch API data if enabled and missing
        if fetch_api and weather_data.get("api_data") is None:
            lat = weather_data["location"]["latitude"]
            lon = weather_data["location"]["longitude"]
            if lat != 0.0 and lon != 0.0:
                api_response = _fetch_weather_from_api(date, lat, lon)
                if api_response:
                    daily_data = api_response.get("daily", {})
                    weather_data["source_type"] = "hybrid"
                    weather_data["api_data"] = {
                        "provider": "open-meteo",
                        "retrieved_at": datetime.utcnow().isoformat() + "Z",
                        "temperature_max_c": daily_data.get(
                            "temperature_2m_max", [None]
                        )[0],
                        "temperature_min_c": daily_data.get(
                            "temperature_2m_min", [None]
                        )[0],
                        "precipitation_mm": daily_data.get("precipitation_sum", [None])[
                            0
                        ],
                        "windspeed_max_kmh": daily_data.get(
                            "windspeed_10m_max", [None]
                        )[0],
                        "cloud_cover_percent": daily_data.get(
                            "cloud_cover_mean", [None]
                        )[0],
                        "raw_response": api_response,
                    }
                    needs_update = True
                    logger.info("    Added API data to %s", weather_file.name)

        # Save if updated
        if needs_update:
            write_json_with_lock(weather_file, weather_data)

        return weather_file

    # Create new weather file
    weather_id = str(ulid.new())
    safe_date = date.replace("-", "")
    safe_place = place_name.replace(" ", "_").replace(",", "")
    filename = f"{safe_date}_{safe_place}_{weather_id[:8]}.json"
    weather_file = weather_dir / filename

    # Look up coordinates from places repository
    latitude, longitude, place_id, country = _lookup_coordinates(
        mention.get("PlaceMentionID"), place_name, places_dir
    )

    # Initialize weather data
    weather_data = {
        "WeatherID": weather_id,
        "date": date,
        "DateID": mention.get("DateMentionID"),
        "location": {
            "place_name": place_name,
            "country": country,
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
            daily_data = api_response.get("daily", {})
            weather_data["source_type"] = "hybrid"
            weather_data["api_data"] = {
                "provider": "open-meteo",
                "retrieved_at": datetime.utcnow().isoformat() + "Z",
                "temperature_max_c": daily_data.get("temperature_2m_max", [None])[0],
                "temperature_min_c": daily_data.get("temperature_2m_min", [None])[0],
                "precipitation_mm": daily_data.get("precipitation_sum", [None])[0],
                "windspeed_max_kmh": daily_data.get("windspeed_10m_max", [None])[0],
                "cloud_cover_percent": daily_data.get("cloud_cover_mean", [None])[0],
                "raw_response": api_response,
            }

    write_json_with_lock(weather_file, weather_data)

    index[weather_key] = filename
    logger.info("    Created weather file: %s", filename)
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
    with open(weather_file, "r", encoding="utf-8") as f:
        weather_data = json.load(f)

    # Check for duplicate mention
    existing = [
        m for m in weather_data["event_mentions"] if m["Sub_eventID"] == sub_event_id
    ]
    if existing:
        logger.info("    Weather already has mention from this sub-event, skipping")
        return

    # Add extracted data if not present
    if not weather_data["extracted_data"]:
        weather_data["extracted_data"] = {
            "description": mention.get("weather_description", ""),
            "temperature": mention.get("temperature"),
            "temperature_unit": mention.get("temperature_unit"),
            "measurement_system": mention.get("measurement_system"),
            "notable_impact": mention.get("notable_impact"),
            "original_text": mention.get("original_text", ""),
            "book": book,
            "author": author,
        }
        if weather_data["api_data"]:
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
    weather_data["event_mentions"].append(event_mention)

    write_json_with_lock(weather_file, weather_data)

    logger.info("    Added mention to %s", weather_file.name)


def create_weather_prompt(
    sub_event: Dict[str, Any], event_id: str, event_name: str
) -> str:
    """Create prompt for weather extraction."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    sub_event_summary = sub_event.get("Sub-event_summary", "")
    fulltext = sub_event.get("Sub-event_fulltext", {})

    text = "\n".join(fulltext.values())

    # Extract available PlaceIDs and DateIDs from sub-event
    places_section = ""
    dates_section = ""

    if "Places" in sub_event and sub_event["Places"]:
        places_list = [
            f"  - {p.get('place_name', 'Unknown')}: {p.get('PlaceMentionID', 'N/A')}"
            for p in sub_event["Places"]
            if isinstance(p, dict)
        ]
        if places_list:
            places_section = "\n\nAvailable Places (link to these):\n" + "\n".join(
                places_list
            )

    if "Dates" in sub_event and sub_event["Dates"]:
        dates_list = [
            f"  - {d.get('date_start', 'Unknown')}: {d.get('DateMentionID', 'N/A')}"
            for d in sub_event["Dates"]
            if isinstance(d, dict)
        ]
        if dates_list:
            dates_section = "\n\nAvailable Dates (link to these):\n" + "\n".join(
                dates_list
            )

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

    return prompt


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
    index_file = weather_dir / "index.json"

    # Load existing index
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    # Validate event_data is a dict
    if not isinstance(event_data, dict):
        logger.error(
            "Invalid event data format in %s: expected dict, got %s",
            event_file,
            type(event_data).__name__,
        )
        return None

    # Get book metadata
    book = ""
    author = ""
    series = ""
    if parsed_file and parsed_file.exists():
        with open(parsed_file, "r", encoding="utf-8") as f:
            parsed_data = json.load(f)
            if isinstance(parsed_data, dict):
                book = parsed_data.get("book", "")
                author = parsed_data.get("author", "")
                series = parsed_data.get("series", "")

    if not book or not author:
        raise ValueError(
            f"Missing required book metadata: book={book!r}, author={author!r}"
        )

    event_name = event_data.get("Chapter", "")
    event_obj = event_data.get("Event", {})
    event_id = event_obj.get("EventID", "")
    sub_events = event_obj.get("Sub-events", [])

    weather_updated = 0

    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_name = sub_event.get("Sub-event_summary", "")
        logger.info("  Processing sub-event %s", sub_event_id)

        prompt = create_weather_prompt(sub_event, event_id, event_name)

        # Retry logic
        for attempt in range(max_retries):
            try:
                weather_output = grok_client.extract_structured(
                    prompt=prompt,
                    schema=WeatherOutput,
                    system_prompt=SYSTEM_PROMPT,
                    use_cache=(attempt == 0),
                    cache_type="weather",
                )

                weather_dict: Dict[str, Any] = weather_output.model_dump(by_alias=True)
                fixed_dict = _fix_invalid_ulids(weather_dict)
                if isinstance(fixed_dict, dict):
                    weather_dict = fixed_dict
                weather_dict = _filter_invalid_weather(weather_dict)

                # Process each weather mention
                for mention in weather_dict.get("Weather_Mentions", []):
                    weather_file = _find_or_create_weather(
                        mention, weather_dir, index, places_dir, fetch_api
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

                num_weather = len(weather_dict.get("Weather_Mentions", []))
                logger.info("  ✓ Extracted %d weather mention(s)", num_weather)
                break

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning("  ⚠ Attempt %d failed: %s", attempt + 1, e)
                    logger.info("  Retrying (%d/%d)...", attempt + 2, max_retries)
                else:
                    logger.error("  ✗ All %d attempts failed: %s", max_retries, e)
                    continue

    # Save index
    write_json_with_lock(index_file, index)

    logger.info("Updated %d weather mentions in central repository", weather_updated)
    return weather_dir if weather_updated > 0 else None
