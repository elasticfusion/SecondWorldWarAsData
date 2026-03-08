"""
Casualty extraction module.

Extracts casualty information from events including wounded, killed,
generic casualties, and prisoners of war.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def extract_casualties(
    event_file: Path,
    output_root: Path,
    grok_client: GrokClient,
) -> List[Dict[str, Any]]:
    """
    Extract casualties from event file.

    Args:
        event_file: Path to event JSON file
        output_root: Output directory root
        grok_client: Grok API client

    Returns:
        List of casualty dictionaries
    """
    # Load event data
    try:
        with open(event_file, encoding="utf-8") as f:
            event_data = json.load(f)
    except Exception as e:
        logger.error("Failed to load event file: %s", e)
        return []

    # Build entity indexes
    dates_index = _build_entity_index(output_root, "dates", "DateID", "date_start")
    places_index = _build_entity_index(output_root, "places", "PlaceID", "current_name")
    people_index = _build_entity_index(output_root, "people", "PersonID", "name")
    people_groups_index = _build_entity_index(
        output_root, "people_groups", "PeopleGroupID", "group_name"
    )
    equipment_index = _build_entity_index(
        output_root, "equipment", "EquipmentID", "common_name"
    )
    weather_index = _build_entity_index(
        output_root, "weather", "WeatherID", "description"
    )

    casualties_dir = output_root / "casualties"
    casualties_dir.mkdir(parents=True, exist_ok=True)

    # Get book and chapter from event data
    book = event_data.get("Book", "Unknown")
    chapter = event_data.get("Chapter", "Unknown")

    # Handle both old and new formats
    if "Event" in event_data:
        event = event_data["Event"]
        sub_events = event.get("Sub-events", [])
        event_id = event.get("EventID")
    else:
        event = event_data
        sub_events = event_data.get("Sub-events", [])
        event_id = event_data.get("EventID")

    casualties = []

    # Process main event
    extracted = _extract_from_event(
        event,
        event_id,
        None,
        book,
        chapter,
        grok_client,
        dates_index,
        places_index,
        people_index,
        people_groups_index,
        equipment_index,
        weather_index,
    )
    casualties.extend(extracted)

    # Process sub-events
    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID")
        try:
            extracted = _extract_from_event(
                sub_event,
                event_id,
                sub_event_id,
                book,
                chapter,
                grok_client,
                dates_index,
                places_index,
                people_index,
                people_groups_index,
                equipment_index,
                weather_index,
            )
            casualties.extend(extracted)
        except Exception as e:
            logger.error(
                "Failed to extract casualties from sub-event %s: %s", sub_event_id, e
            )
            continue  # Skip this sub-event, continue with next

    # Save casualties
    for casualty in casualties:
        try:
            _save_casualty(casualty, casualties_dir)
        except Exception as e:
            casualty_id = casualty.get("CasualtyID", "unknown")
            logger.error("Failed to save casualty %s: %s", casualty_id, e)
            continue  # Skip this one, continue with next

    return casualties


def _extract_from_event(
    event: Dict[str, Any],
    event_id: str,
    sub_event_id: Optional[str],
    book: str,
    chapter: str,
    grok_client: GrokClient,
    dates_index: Dict[str, Any],
    places_index: Dict[str, Any],
    people_index: Dict[str, Any],
    people_groups_index: Dict[str, Any],
    equipment_index: Dict[str, Any],
    weather_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Extract casualties from a single event or sub-event."""
    # Get text from either description or Sub-event_fulltext
    description = event.get("description", "")
    if not description and "Sub-event_fulltext" in event:
        fulltext = event.get("Sub-event_fulltext", {})
        description = " ".join(str(v) for v in fulltext.values())

    paragraph_number = event.get("paragraph_number")

    # Check if event mentions casualties
    if not _has_casualty_mention(description):
        return []

    logger.info("Extracting casualties from event %s", event_id)

    prompt = _build_extraction_prompt(
        description,
        dates_index,
        places_index,
        people_index,
        people_groups_index,
        equipment_index,
    )

    # Retry logic with exponential backoff
    max_retries = 3
    casualties_data = []

    for attempt in range(max_retries):
        try:
            use_cache = attempt == 0  # First attempt uses cache
            response = grok_client.chat_completion(
                prompt, use_cache=use_cache, cache_type="casualties"
            )
            casualties_data = _parse_response(response)
            break  # Success, exit retry loop
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning("  ⚠ Attempt %d failed: %s", attempt + 1, e)
                logger.info("  Retrying (%d/%d)...", attempt + 2, max_retries)
            else:
                logger.error("  ✗ All %d attempts failed: %s", max_retries, e)
                return []  # Return empty list on failure

    casualties = []
    for casualty_data in casualties_data:
        try:
            casualty = _build_casualty(
                casualty_data,
                event_id,
                sub_event_id,
                book,
                chapter,
                paragraph_number,
                dates_index,
                places_index,
                people_index,
                people_groups_index,
                equipment_index,
                weather_index,
            )
            casualties.append(casualty)
        except Exception as e:
            logger.warning("Failed to build casualty from data: %s", e)
            continue  # Skip this one, continue with next

    return casualties


def _has_casualty_mention(text: str) -> bool:
    """Check if text mentions casualties."""
    keywords = [
        "casualt",
        "killed",
        "wounded",
        "dead",
        "injur",
        "prison",
        "captured",
        "pow",
        "losses",
        "kia",
        "wia",
    ]
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in keywords)


def _build_extraction_prompt(
    description: str,
    dates_index: Dict[str, Any],
    places_index: Dict[str, Any],
    people_index: Dict[str, Any],
    people_groups_index: Dict[str, Any],
    equipment_index: Dict[str, Any],
) -> str:
    """Build prompt for casualty extraction."""
    return f"""Extract casualty information from this event description.

Event: {description}

For each casualty incident, provide:
1. type: wounded|killed|casualties|pow
2. description: Brief description
3. count: {{killed, wounded, missing, captured, total}} (only if numbers mentioned)
4. impacted_organizations: Organizations with nationality (ISO 3166-1 alpha-3) and role
   - For POW: MUST include both "captured" and "captor" organizations
5. impacted_people: People involved (if named)
6. impacted_places: Places involved
7. impacted_equipment: Equipment losses (if mentioned)

Available entities:
- Dates: {list(dates_index.keys())[:10]}
- Places: {list(places_index.keys())[:10]}
- People: {list(people_index.keys())[:10]}
- Organizations: {list(people_groups_index.keys())[:10]}
- Equipment: {list(equipment_index.keys())[:10]}

Return JSON array of casualties. Return empty array [] if no casualties found.
"""


def _parse_response(response: str) -> List[Dict[str, Any]]:
    """Parse Grok response into casualty data."""
    try:
        # Extract JSON from response
        start = response.find("[")
        end = response.rfind("]") + 1
        if start == -1 or end == 0:
            logger.debug("No JSON array found in response")
            return []

        json_str = response[start:end]
        data = json.loads(json_str)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse casualty response: %s", e)
        return []
    except Exception as e:
        logger.error("Unexpected error parsing response: %s", e)
        return []


def _build_casualty(
    casualty_data: Dict[str, Any],
    event_id: str,
    sub_event_id: Optional[str],
    book: str,
    chapter: str,
    paragraph_number: Optional[int],
    dates_index: Dict[str, Any],
    places_index: Dict[str, Any],
    people_index: Dict[str, Any],
    people_groups_index: Dict[str, Any],
    equipment_index: Dict[str, Any],
    weather_index: Dict[str, Any],
) -> Dict[str, Any]:
    """Build casualty JSON structure."""
    casualty_id = str(ulid.new())

    casualty = {
        "CasualtyID": casualty_id,
        "type": casualty_data.get("type", "casualties"),
        "description": casualty_data.get("description", ""),
        "event_context": {"EventID": event_id, "Sub-eventID": sub_event_id},
        "source": {
            "book": book,
            "chapter": chapter,
            "paragraph_number": paragraph_number,
        },
    }

    # Add count if present
    if "count" in casualty_data:
        casualty["count"] = casualty_data["count"]

    # Add date
    if "date" in casualty_data:
        casualty["date"] = _resolve_date(casualty_data["date"], dates_index)

    # Add impacted organizations
    if "impacted_organizations" in casualty_data:
        casualty["impacted_organizations"] = _resolve_organizations(
            casualty_data["impacted_organizations"], people_groups_index
        )

    # Add impacted people
    if "impacted_people" in casualty_data:
        casualty["impacted_people"] = _resolve_people(
            casualty_data["impacted_people"], people_index
        )

    # Add impacted places
    if "impacted_places" in casualty_data:
        casualty["impacted_places"] = _resolve_places(
            casualty_data["impacted_places"], places_index
        )

    # Add impacted equipment
    if "impacted_equipment" in casualty_data:
        casualty["impacted_equipment"] = _resolve_equipment(
            casualty_data["impacted_equipment"], equipment_index
        )

    # Add weather
    if "weather" in casualty_data:
        casualty["weather_conditions"] = _resolve_weather(
            casualty_data["weather"], weather_index
        )

    return casualty


def _resolve_date(
    date_ref: Any, dates_index: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Resolve date reference to DateID."""
    if isinstance(date_ref, dict) and "DateID" in date_ref:
        return date_ref
    if isinstance(date_ref, str) and date_ref in dates_index:
        date_data = dates_index[date_ref]
        return {
            "DateID": date_data.get("DateID"),
            "date_string": date_data.get("date_string"),
            "precision": date_data.get("precision"),
        }
    return None


def _resolve_organizations(
    orgs: List[Any], people_groups_index: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve organization references to PeopleGroupIDs."""
    resolved = []
    for org in orgs:
        if isinstance(org, dict):
            org_name = org.get("name", "")
            org_id = _find_organization_id(org_name, people_groups_index)
            resolved.append(
                {
                    "PeopleGroupID": org_id or str(ulid.new()),
                    "name": org_name,
                    "nationality": org.get("nationality", ""),
                    "role": org.get("role", ""),
                }
            )
    return resolved


def _resolve_people(
    people: List[Any], people_index: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve people references to PersonIDs."""
    resolved = []
    for person in people:
        if isinstance(person, dict):
            person_name = person.get("name", "")
            person_id = _find_person_id(person_name, people_index)
            resolved.append(
                {
                    "PersonID": person_id or str(ulid.new()),
                    "name": person_name,
                    "casualty_type": person.get("casualty_type", ""),
                }
            )
    return resolved


def _resolve_places(
    places: List[Any], places_index: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve place references to PlaceIDs."""
    resolved = []
    for place in places:
        if isinstance(place, dict):
            place_name = place.get("name", "")
            place_id = _find_place_id(place_name, places_index)
            resolved.append(
                {"PlaceID": place_id or str(ulid.new()), "name": place_name}
            )
    return resolved


def _resolve_equipment(
    equipment: List[Any], equipment_index: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve equipment references to EquipmentIDs."""
    resolved = []
    for equip in equipment:
        if isinstance(equip, dict):
            equip_name = equip.get("common_name", "")
            equip_id = _find_equipment_id(equip_name, equipment_index)
            resolved.append(
                {
                    "EquipmentID": equip_id or str(ulid.new()),
                    "common_name": equip_name,
                    "count_lost": equip.get("count_lost", 0),
                }
            )
    return resolved


def _resolve_weather(
    weather_ref: Any, weather_index: Dict[str, Any]
) -> Optional[Dict[str, str]]:
    """Resolve weather reference to WeatherID."""
    if isinstance(weather_ref, dict) and "WeatherID" in weather_ref:
        return weather_ref
    if isinstance(weather_ref, str) and weather_ref in weather_index:
        return {"WeatherID": weather_index[weather_ref].get("WeatherID")}
    return None


def _find_organization_id(name: str, index: Dict[str, Any]) -> Optional[str]:
    """Find organization ID by name."""
    for org_data in index.values():
        if org_data.get("name", "").lower() == name.lower():
            return org_data.get("PeopleGroupID")
    return None


def _find_person_id(name: str, index: Dict[str, Any]) -> Optional[str]:
    """Find person ID by name."""
    for person_data in index.values():
        if person_data.get("name", "").lower() == name.lower():
            return person_data.get("PersonID")
    return None


def _find_place_id(name: str, index: Dict[str, Any]) -> Optional[str]:
    """Find place ID by name."""
    for place_data in index.values():
        if place_data.get("name", "").lower() == name.lower():
            return place_data.get("PlaceID")
    return None


def _find_equipment_id(name: str, index: Dict[str, Any]) -> Optional[str]:
    """Find equipment ID by name."""
    for equip_data in index.values():
        if equip_data.get("common_name", "").lower() == name.lower():
            return equip_data.get("EquipmentID")
    return None


def _save_casualty(casualty: Dict[str, Any], output_dir: Path) -> None:
    """Save casualty to JSON file."""
    casualty_id = casualty["CasualtyID"]
    casualty_type = casualty["type"]
    filename = f"{casualty_type}_{casualty_id}.json"
    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(casualty, f, indent=2, ensure_ascii=False)

    logger.info("Saved casualty: %s", filename)


def _load_event_data(event_file: Path) -> Optional[Dict[str, Any]]:
    """Load event data from JSON file."""
    try:
        with open(event_file, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s", event_file.name, e)
        return None
    except Exception as e:
        logger.error("Failed to load %s: %s", event_file.name, e)
        return None


def _build_entity_index(
    output_root: Path, entity_type: str, id_field: str, name_field: str
) -> Dict[str, Any]:
    """Build entity index from JSON files."""
    index: Dict[str, Any] = {}
    entity_dir = output_root / entity_type
    if not entity_dir.exists():
        return index

    for json_file in entity_dir.glob("*.json"):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
                entity_id = data.get(id_field)
                name = data.get(name_field)
                if entity_id and name:
                    index[name] = data
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load %s: %s", json_file.name, e)

    return index
