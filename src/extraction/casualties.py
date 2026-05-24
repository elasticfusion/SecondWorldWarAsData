"""
Casualty extraction module.

Extracts casualty information from events including wounded, killed,
generic casualties, and prisoners of war.
"""

import json
import logging
import re
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
    from src.utils.entity_index import build_name_index

    dates_index = build_name_index(output_root / "dates", "DateID", "date_start")
    places_index = build_name_index(output_root / "places", "PlaceID", "name")
    people_index = build_name_index(output_root / "people", "PersonID", "name")
    people_groups_index = build_name_index(
        output_root / "people_groups", "PeopleGroupID", "group_name"
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

    # Build entity context with name:ID pairs for cross-referencing
    def _format_index(index, limit=50):
        items = [f"{name}: {eid}" for name, eid in list(index.items())[:limit]]
        return "\n    ".join(items) if items else "(none)"

    entity_context = (
        f"Available entities (COPY these IDs exactly — do NOT generate new ones):\n"
        f"  Organizations:\n    {_format_index(people_groups_index)}\n"
        f"  People:\n    {_format_index(people_index)}\n"
        f"  Places:\n    {_format_index(places_index)}\n"
    )

    sub_event_block = "\n\n".join(
        f"--- Sub-event [{seid}] ---\n{text}" for seid, text in relevant
    )

    prompt = f"""Extract personnel casualty information from these sub-events.
Casualties are about PEOPLE only — not equipment, vehicles, or materiel.

{entity_context}

{sub_event_block}

For each casualty incident, provide:
1. type: wounded|killed|casualties|pow|missing
2. side: allied|axis|civilian|unknown (who SUFFERED the casualties)
3. description: Brief description
4. count: {{killed, wounded, missing, captured, total}} (only if numbers mentioned)
5. date_string: The date as mentioned in text (e.g. "18 July 1944")
6. impacted_organizations: Array of {{"name": "...", "nationality": "USA", "role": "attacking_force"}}
   - nationality: ISO 3166-1 alpha-3
   - role: one of attacking_force, defending_force, captured, captor, suffered_casualties
   - For POW: MUST include both "captured" and "captor" organizations
7. impacted_people: Array of {{"name": "Captain Smith", "casualty_type": "killed"}}
8. impacted_places: Array of {{"name": "Omaha Beach"}}

Return JSON object keyed by sub-event ID:
{{"<Sub-eventID>": [<casualty items>], ...}}
Return empty arrays for sub-events with no casualties."""

    try:
        from src.utils.prompt_loader import render_prompt as _rp

        prompt = _rp(
            "casualties", entity_context=entity_context, sub_event_block=sub_event_block
        )
    except Exception:
        pass

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


_COUNT_RE = re.compile(r"[\d,]+")
_QUALIFIER_PATTERNS = [
    (lambda t: t.startswith(">") or "more than" in t, "greater_than"),
    (lambda t: t.startswith("<") or "fewer than" in t or "less than" in t, "less_than"),
    (
        lambda t: any(
            w in t
            for w in (
                "nearly",
                "almost",
                "close to",
                "about",
                "approximately",
                "around",
            )
        ),
        "approximately",
    ),
]


def _normalize_count_value(value):
    """Normalize a single count value to {value, qualifier}."""
    if isinstance(value, (int, float)):
        return {"value": int(value), "qualifier": "exact"}
    if not isinstance(value, str):
        return {"value": 0, "qualifier": "unknown"}

    text = value.strip().lower()
    nums = _COUNT_RE.findall(text)
    number = int(nums[0].replace(",", "")) if nums else 0

    for check, qualifier in _QUALIFIER_PATTERNS:
        if check(text):
            return {"value": number, "qualifier": qualifier}

    if number:
        return {"value": number, "qualifier": "approximately"}
    return {"value": 0, "qualifier": "unknown", "original_text": value}


def _normalize_counts(count):
    """Normalize all values in a count dict."""
    if not isinstance(count, dict):
        return count
    return {k: _normalize_count_value(v) for k, v in count.items()}


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
        "kia",
        "wia",
        "missing in action",
    ]
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in keywords)


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
) -> None:
    """Resolve and attach all impacted entity arrays to casualty."""
    resolvers = {
        "impacted_organizations": (
            lambda d: _resolve_organizations(d, people_groups_index)
        ),
        "impacted_people": lambda d: _resolve_people(d, people_index),
        "impacted_places": lambda d: _resolve_places(d, places_index),
    }
    for field, resolver in resolvers.items():
        if field in casualty_data:
            casualty[field] = resolver(casualty_data[field])


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
) -> Dict[str, Any]:
    """Build casualty JSON structure."""
    casualty = {
        "CasualtyID": str(ulid.new()),
        "type": casualty_data.get("type", "casualties"),
        "side": (
            casualty_data.get("side", "unknown")
            if casualty_data.get("side", "unknown") in VALID_SIDES
            else "unknown"
        ),
        "description": casualty_data.get("description", ""),
        "event_context": {"EventID": event_id, "Sub-eventID": sub_event_id},
        "source": {
            "EventID": event_id,
            "Sub-eventID": sub_event_id,
            "book": book,
            "chapter": chapter,
            "paragraph_number": paragraph_number,
        },
    }

    if "count" in casualty_data:
        casualty["count"] = _normalize_counts(casualty_data["count"])

    date = _resolve_casualty_date(casualty_data, dates_index)
    if date:
        casualty["date"] = date

    _resolve_impacted_entities(
        casualty_data,
        casualty,
        places_index,
        people_index,
        people_groups_index,
    )

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
            "iso_date": date_ref,
            "precision": "day",
        }

    # Fuzzy match: parse natural language date to ISO and look up
    iso = _parse_date_string(date_ref)
    if iso and iso in dates_index:
        date_data = dates_index[iso]
        return {
            "DateID": date_data.get("DateID"),
            "date_string": date_ref,
            "iso_date": iso,
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

VALID_SIDES = {"allied", "axis", "civilian", "unknown"}

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
                    "PeopleGroupID": org_id,
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
                    "PersonID": person_id,
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
            resolved.append({"PlaceID": place_id, "name": place_name})
    return resolved


def _find_organization_id(name: str, index: Dict[str, str]) -> Optional[str]:
    """Find organization ID by name."""
    return index.get(name.lower())


def _find_person_id(name: str, index: Dict[str, str]) -> Optional[str]:
    """Find person ID by name."""
    return index.get(name.lower())


def _find_place_id(name: str, index: Dict[str, str]) -> Optional[str]:
    """Find place ID by name."""
    return index.get(name.lower())


def _save_casualty(casualty: Dict[str, Any], output_dir: Path) -> None:
    """Save casualty to JSON file."""
    casualty_id = casualty["CasualtyID"]
    casualty_type = casualty["type"]
    filename = f"{casualty_type}_{casualty_id}.json"
    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(casualty, f, indent=2, ensure_ascii=False)

    logger.info("Saved casualty: %s", filename)
