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
from src.json_schemas import CASUALTY_ITEM_SCHEMA
from src.utils.json_validator import _fix_invalid_ulids

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
    places_index = _build_entity_index(output_root, "places", "PlaceID", "name")
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
    book = event_data.get("Book") or _book_name_from_path(event_file)
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

    # Batch extract from all sub-events with casualty mentions
    batch_results = _batch_extract_casualties(
        sub_events,
        grok_client,
        dates_index,
        places_index,
        people_index,
        people_groups_index,
        equipment_index,
    )

    # Process results for each sub-event
    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID")
        casualties_data = batch_results.get(sub_event_id, [])

        for casualty_data in casualties_data:
            try:
                casualty = _build_casualty(
                    casualty_data,
                    event_id,
                    sub_event_id,
                    book,
                    chapter,
                    _first_paragraph_number(sub_event),
                    dates_index,
                    places_index,
                    people_index,
                    people_groups_index,
                    equipment_index,
                    weather_index,
                )
                casualties.append(casualty)
            except Exception as e:
                logger.error("Failed to build casualty: %s", e)

    # Save casualties
    for casualty in casualties:
        try:
            _save_casualty(casualty, casualties_dir)
        except Exception as e:
            casualty_id = casualty.get("CasualtyID", "unknown")
            logger.error("Failed to save casualty %s: %s", casualty_id, e)

    return casualties


def _batch_extract_casualties(
    sub_events: List[Dict[str, Any]],
    grok_client: GrokClient,
    dates_index: Dict[str, Any],
    places_index: Dict[str, Any],
    people_index: Dict[str, Any],
    people_groups_index: Dict[str, Any],
    equipment_index: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Extract casualties from all sub-events in a single API call.

    Returns dict mapping sub_event_id → list of casualty items.
    """
    # Filter to sub-events that mention casualties
    relevant = []
    for se in sub_events:
        text = se.get("description", "")
        if not text and "Sub-event_fulltext" in se:
            fulltext = se.get("Sub-event_fulltext", {})
            text = " ".join(str(v) for v in fulltext.values())
        if _has_casualty_mention(text):
            relevant.append((se.get("Sub-eventID", ""), text))

    if not relevant:
        return {}

    # Build batched prompt
    entity_context = (
        f"Available entities:\n"
        f"- Dates: {list(dates_index.keys())[:10]}\n"
        f"- Places: {list(places_index.keys())[:10]}\n"
        f"- People: {list(people_index.keys())[:10]}\n"
        f"- Organizations: {list(people_groups_index.keys())[:10]}\n"
        f"- Equipment: {list(equipment_index.keys())[:10]}\n"
    )

    sub_event_block = "\n\n".join(
        f"--- Sub-event [{seid}] ---\n{text}" for seid, text in relevant
    )

    prompt = f"""Extract casualty information from these sub-events.

{entity_context}

{sub_event_block}

For each casualty incident, provide:
1. type: wounded|killed|casualties|pow
2. description: Brief description
3. count: {{killed, wounded, missing, captured, total}} (only if numbers mentioned)
4. date_string: The date as mentioned in text (e.g. "18 July 1944")
5. impacted_organizations: Array of {{"name": "...", "nationality": "USA", "role": "attacking_force"}}
   - nationality: ISO 3166-1 alpha-3
   - role: one of attacking_force, defending_force, captured, captor, suffered_casualties
   - For POW: MUST include both "captured" and "captor" organizations
6. impacted_people: Array of {{"name": "Captain Smith", "casualty_type": "killed"}}
7. impacted_places: Array of {{"name": "Omaha Beach"}}
8. impacted_equipment: Array of {{"common_name": "M4 Sherman", "count_lost": 5}}

Return JSON object keyed by sub-event ID:
{{"<Sub-eventID>": [<casualty items>], ...}}
Return empty arrays for sub-events with no casualties."""

    return _call_and_parse_casualties(grok_client, prompt)


def _call_and_parse_casualties(
    grok_client: GrokClient, prompt: str
) -> Dict[str, List[Dict[str, Any]]]:
    """Call Grok API and parse batched casualty response."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = grok_client.chat_completion(
                prompt, use_cache=(attempt == 0), cache_type="casualties"
            )
            return _parse_casualty_response(response)
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    "  ⚠ Batch casualties attempt %d failed: %s", attempt + 1, e
                )
            else:
                logger.error("  ✗ All %d batch attempts failed: %s", max_retries, e)
    return {}


def _parse_casualty_response(response: str) -> Dict[str, List[Dict[str, Any]]]:
    """Parse raw Grok response into validated casualty items by sub-event."""
    start = response.find("{")
    end = response.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    parsed = json.loads(response[start:end])
    if not isinstance(parsed, dict):
        return {}
    result = {}
    for seid, items in parsed.items():
        if isinstance(items, list):
            items = _fix_invalid_ulids(items)
            result[seid] = _validate_items(items)
    return result


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
            casualties_data = _fix_invalid_ulids(casualties_data)
            casualties_data = _validate_items(casualties_data)
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


def _book_name_from_path(event_file: Path) -> str:
    """Derive book name from event file parent directory (e.g. BreakoutAndPursuit)."""
    import re

    dir_name = event_file.parent.name
    # Insert spaces before capitals: BreakoutAndPursuit → Breakout And Pursuit
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", dir_name) or "Unknown"


def _first_paragraph_number(sub_event: Dict[str, Any]) -> Optional[int]:
    """Extract first paragraph number from sub-event fulltext keys."""
    fulltext = sub_event.get("Sub-event_fulltext", {})
    for key in fulltext:
        # Keys like "Paragraph_7"
        if key.startswith("Paragraph_"):
            try:
                return int(key.split("_", 1)[1])
            except (ValueError, IndexError):
                pass
    return None


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
4. date_string: The date as mentioned in text (e.g. "18 July 1944")
5. impacted_organizations: Array of {{"name": "...", "nationality": "USA", "role": "attacking_force"}}
   - nationality: ISO 3166-1 alpha-3
   - role: one of attacking_force, defending_force, captured, captor, suffered_casualties
   - For POW: MUST include both "captured" and "captor" organizations
6. impacted_people: Array of {{"name": "Captain Smith", "casualty_type": "killed"}}
7. impacted_places: Array of {{"name": "Omaha Beach"}}
8. impacted_equipment: Array of {{"common_name": "M4 Sherman", "count_lost": 5}}

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


def _validate_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate casualty items against schema, dropping invalid ones."""
    from jsonschema import validate, ValidationError

    valid = []
    for item in items:
        try:
            validate(instance=item, schema=CASUALTY_ITEM_SCHEMA)
            valid.append(item)
        except ValidationError as e:
            logger.warning("Dropping invalid casualty item: %s", e.message)
    return valid


def _resolve_casualty_date(
    casualty_data: Dict[str, Any], dates_index: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Resolve date from casualty data (structured or string)."""
    for key in ("date", "date_string"):
        if key in casualty_data:
            resolved = _resolve_date(casualty_data[key], dates_index)
            if resolved:
                return resolved
    return None


def _resolve_impacted_entities(
    casualty_data: Dict[str, Any],
    casualty: Dict[str, Any],
    places_index: Dict[str, Any],
    people_index: Dict[str, Any],
    people_groups_index: Dict[str, Any],
    equipment_index: Dict[str, Any],
    weather_index: Dict[str, Any],
) -> None:
    """Resolve and attach all impacted entity arrays to casualty."""
    resolvers = {
        "impacted_organizations": (
            lambda d: _resolve_organizations(d, people_groups_index)
        ),
        "impacted_people": lambda d: _resolve_people(d, people_index),
        "impacted_places": lambda d: _resolve_places(d, places_index),
        "impacted_equipment": lambda d: _resolve_equipment(d, equipment_index),
    }
    for field, resolver in resolvers.items():
        if field in casualty_data:
            casualty[field] = resolver(casualty_data[field])
    if "weather" in casualty_data:
        resolved = _resolve_weather(casualty_data["weather"], weather_index)
        if resolved:
            casualty["weather_conditions"] = resolved


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
    casualty = {
        "CasualtyID": str(ulid.new()),
        "type": casualty_data.get("type", "casualties"),
        "description": casualty_data.get("description", ""),
        "event_context": {"EventID": event_id, "Sub-eventID": sub_event_id},
        "source": {
            "book": book,
            "chapter": chapter,
            "paragraph_number": paragraph_number,
        },
    }

    if "count" in casualty_data:
        casualty["count"] = casualty_data["count"]

    date = _resolve_casualty_date(casualty_data, dates_index)
    if date:
        casualty["date"] = date

    _resolve_impacted_entities(
        casualty_data,
        casualty,
        places_index,
        people_index,
        people_groups_index,
        equipment_index,
        weather_index,
    )

    return casualty

    return casualty


def _resolve_date(
    date_ref: Any, dates_index: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Resolve date reference to DateID.

    Handles: dict with DateID, ISO date string, or natural language date string.
    """
    if isinstance(date_ref, dict) and "DateID" in date_ref:
        return date_ref

    if not isinstance(date_ref, str) or not date_ref:
        return None

    # Direct match on ISO date key (e.g. "1944-07-18")
    if date_ref in dates_index:
        date_data = dates_index[date_ref]
        return {
            "DateID": date_data.get("DateID"),
            "date_string": date_ref,
            "precision": "day",
        }

    # Fuzzy match: parse natural language date to ISO and look up
    iso = _parse_date_string(date_ref)
    if iso and iso in dates_index:
        date_data = dates_index[iso]
        return {
            "DateID": date_data.get("DateID"),
            "date_string": date_ref,
            "precision": "day" if len(iso) == 10 else "month",
        }

    # No match — still record the date string without a DateID
    return {"DateID": None, "date_string": date_ref, "precision": "unknown"}


def _parse_date_string(date_str: str) -> Optional[str]:
    """Parse natural language date to ISO format (best effort)."""
    import re
    from datetime import datetime

    date_str = date_str.strip()
    # Try common patterns: "18 July 1944", "July 1944", "6 June 1944"
    for fmt in ("%d %B %Y", "%B %d, %Y", "%d %b %Y", "%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if "%d" in fmt:
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-01")
        except ValueError:
            continue
    # Try extracting year-month-day with regex
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", date_str)
    if m:
        try:
            dt = datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y"
            )
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


VALID_ROLES = {
    "attacking_force",
    "defending_force",
    "captured",
    "captor",
    "suffered_casualties",
}

# Map freeform LLM roles to controlled vocabulary
_ROLE_MAP = {
    "attacker": "attacking_force",
    "attackers": "attacking_force",
    "attacking": "attacking_force",
    "attacking force": "attacking_force",
    "assaulting": "attacking_force",
    "assaulting force": "attacking_force",
    "assault force": "attacking_force",
    "assault": "attacking_force",
    "defender": "defending_force",
    "defenders": "defending_force",
    "defending": "defending_force",
    "captured": "captured",
    "captor": "captor",
    "suffered casualties": "suffered_casualties",
    "suffered_casualties": "suffered_casualties",
    "sustained casualties": "suffered_casualties",
    "sustained-casualties": "suffered_casualties",
    "suffered-casualties": "suffered_casualties",
    "suffered": "suffered_casualties",
    "suffered heavy casualties": "suffered_casualties",
    "suffered losses": "suffered_casualties",
    "suffered heavy losses": "suffered_casualties",
    "suffered light casualties": "suffered_casualties",
    "suffered severe casualties": "suffered_casualties",
    "suffered wounded": "suffered_casualties",
    "suffered killed": "suffered_casualties",
    "victim": "suffered_casualties",
    "wounded": "suffered_casualties",
    "killed": "suffered_casualties",
    "overrun": "suffered_casualties",
}


def _normalize_role(role: str) -> str:
    """Normalize freeform role to controlled vocabulary."""
    role_lower = role.lower().strip()
    if role_lower in VALID_ROLES:
        return role_lower
    return _ROLE_MAP.get(role_lower, "suffered_casualties")


def _resolve_organizations(
    orgs: List[Any], people_groups_index: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve organization references to PeopleGroupIDs."""
    resolved = []
    for org in orgs:
        if isinstance(org, dict):
            org_name = org.get("name", "")
            if not org_name:
                continue
            org_id = _find_organization_id(org_name, people_groups_index)
            resolved.append(
                {
                    "PeopleGroupID": org_id or str(ulid.new()),
                    "name": org_name,
                    "nationality": org.get("nationality", ""),
                    "role": _normalize_role(org.get("role", "")),
                }
            )
    return resolved


def _resolve_people(
    people: List[Any], people_index: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Resolve people references to PersonIDs."""
    resolved = []
    for person in people:
        if isinstance(person, str):
            person = {"name": person, "casualty_type": ""}
        if isinstance(person, dict):
            person_name = person.get("name", "")
            if not person_name:
                continue
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
        if isinstance(place, str):
            place = {"name": place}
        if isinstance(place, dict):
            place_name = place.get("name", "")
            if not place_name:
                continue
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
        if isinstance(equip, str):
            equip = {"common_name": equip}
        if isinstance(equip, dict):
            # Handle LLM returning "name" instead of "common_name"
            equip_name = equip.get("common_name") or equip.get("name", "")
            if not equip_name:
                continue
            equip_id = _find_equipment_id(equip_name, equipment_index)
            count_lost = equip.get("count_lost") or equip.get("count", 0)
            resolved.append(
                {
                    "EquipmentID": equip_id or str(ulid.new()),
                    "common_name": equip_name,
                    "count_lost": count_lost,
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

    # Fallback field names for transition compatibility
    fallbacks = {"date_start": "date", "group_name": "name"}
    fallback = fallbacks.get(name_field)

    for json_file in entity_dir.glob("*.json"):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
                entity_id = data.get(id_field)
                name = data.get(name_field) or (
                    data.get(fallback) if fallback else None
                )
                if entity_id and name:
                    index[name] = data
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load %s: %s", json_file.name, e)

    return index
