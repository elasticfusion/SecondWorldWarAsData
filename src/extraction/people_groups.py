#!/usr/bin/env python3
"""
Extract people groups (military units, countries, alliances, organizations) from events.

Similar to people extraction, creates individual files per group in output/people_groups/
with central index for lookups.
"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from ulid import new as new_ulid

from src.grok_client import GrokClient
from src.json_schemas import PEOPLE_GROUP_ITEM_SCHEMA
from src.utils.json_validator import _fix_invalid_ulids
from src.utils.text_utils import normalize_name

logger = logging.getLogger(__name__)


@lru_cache(maxsize=5000)
def _normalize_name(name: str) -> str:
    """Normalize group name for index lookup (deprecated - use text_utils.normalize_name)."""
    return normalize_name(name)


def _name_to_filename(name: str, group_id: str) -> str:
    """Convert group name to safe filename with ULID prefix."""
    # Take first 8 chars of ULID for uniqueness
    prefix = group_id[:8]
    # Sanitize name for filename
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    safe_name = safe_name.replace(" ", "_")
    return f"{safe_name}_{prefix}.json"


def _update_index(index_file: Path, name: str, filename: str):
    """Update index.json with name -> filename mapping."""
    index = {}
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)

    normalized = _normalize_name(name)
    index[normalized] = filename

    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)


def _merge_list_by_id(existing: list, new_items: list, id_key: str) -> list:
    """Merge lists, deduplicating by ID key."""
    existing_ids = {item.get(id_key) for item in existing if item.get(id_key)}
    for item in new_items:
        if item.get(id_key) and item[id_key] not in existing_ids:
            existing.append(item)
    return existing


def _merge_string_sets(existing: list, new_items: list) -> list:
    """Merge string lists as sorted sets."""
    return sorted(set(existing) | set(new_items))


def _merge_group(existing: Dict, new_group: Dict) -> Dict:
    """Merge new group data into existing group."""
    merged = existing.copy()

    # Merge event mentions
    merged["event_mentions"] = _merge_list_by_id(
        merged.get("event_mentions", []),
        new_group.get("event_mentions", []),
        "MentionID",
    )

    # Update description if new one is longer
    if len(new_group.get("description", "")) > len(existing.get("description", "")):
        merged["description"] = new_group["description"]

    # Merge member countries and sub-organizations
    if "member_countries" in new_group:
        merged["member_countries"] = _merge_string_sets(
            merged.get("member_countries", []), new_group["member_countries"]
        )

    if "sub_organizations" in new_group:
        merged["sub_organizations"] = _merge_string_sets(
            merged.get("sub_organizations", []), new_group["sub_organizations"]
        )

    # Merge members (people)
    if "members" in new_group:
        merged["members"] = _merge_list_by_id(
            merged.get("members", []), new_group["members"], "PersonID"
        )

    return merged


def _is_already_processed(groups_dir: Path, event_file: Path) -> bool:
    """Check if event file was already processed."""
    processed_registry = groups_dir / ".processed_events.json"
    if not processed_registry.exists():
        return False
    with open(processed_registry, "r", encoding="utf-8") as f:
        processed = json.load(f)
    return str(event_file.resolve()) in processed


def _mark_as_processed(
    groups_dir: Path, event_file: Path, groups_count: int, extracted_date: str
):
    """Mark event file as processed."""
    processed_registry = groups_dir / ".processed_events.json"
    processed = {}
    if processed_registry.exists():
        with open(processed_registry, "r", encoding="utf-8") as f:
            processed = json.load(f)
    processed[str(event_file.resolve())] = {
        "processed_at": extracted_date,
        "groups_extracted": groups_count,
    }
    with open(processed_registry, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2)


def _build_extraction_prompt(event_data: dict) -> str:
    """Build prompt for people groups extraction."""
    return f"""Extract all people groups mentioned in these events.

For each group, provide:
- group_name: Official name
- group_type: One of: country, alliance, military_unit, political_party, government_organization, anti_government_organization, religious_organization
- military_hierarchy: (if military_unit) squad/platoon/company/battalion/regiment/brigade/division/corps/army/army_group
- country_of_origin: ISO 3166-1 alpha-3 country code (e.g., USA, GBR, DEU, FRA, ITA, JPN)
- alliance_membership: List of alliances (e.g., ["Axis Powers"], ["Allied Powers"])
- description: Brief description
- parent_organization: (if applicable) Parent unit/organization
- member_countries: (if alliance) List of member countries
- sub_organizations: (if applicable) List of sub-organizations
- common_name: (if different from official name)
- members: List of people associated with this group (commanders, leaders, key personnel)

For each member, include:
- PersonID: ULID of the person (if mentioned in events)
- name: Person's name
- role: Their role/position in the group (e.g., "Commander", "Member", "Leader")
- date_range: When they held this position (if mentioned)

For each mention, include:
- Event_Name, EventID, Sub-event_Name, Sub-eventID
- date (if mentioned)
- context: Role/action in this event
- original_text: Exact quote mentioning the group

Events:
{json.dumps(event_data, indent=2)}

Return ONLY valid JSON matching this structure:
{{
  "People_Groups": [
    {{
      "GroupID": "01...",
      "group_name": "...",
      "group_type": "...",
      "country_of_origin": "...",
      "alliance_membership": [...],
      "source_language": "English",
      "description": "...",
      "members": [
        {{
          "PersonID": "01...",
          "name": "...",
          "role": "...",
          "date_range": "..."
        }}
      ],
      "event_mentions": [
        {{
          "MentionID": "01...",
          "Event_Name": "...",
          "EventID": "01...",
          "Sub-event_Name": "...",
          "Sub-eventID": "01...",
          "date": "YYYY-MM-DD",
          "DateMentionID": "01...",
          "context": "...",
          "original_text": "..."
        }}
      ]
    }}
  ]
}}"""


def _parse_groups_response(response: str) -> list:
    """Parse API response and extract groups list."""
    response_clean = response.strip()
    if response_clean.startswith("```"):
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", response_clean, re.DOTALL)
        if match:
            response_clean = match.group(1)
    data = json.loads(response_clean)
    return data.get("People_Groups", [])


def _validate_group_items(items: list) -> list:
    """Validate group items against schema, dropping invalid ones."""
    from jsonschema import validate, ValidationError

    valid = []
    for item in items:
        try:
            validate(instance=item, schema=PEOPLE_GROUP_ITEM_SCHEMA)
            valid.append(item)
        except ValidationError as e:
            logger.warning("Dropping invalid group item: %s", e.message)
    return valid


def _save_group(
    groups_dir: Path, index_file: Path, group: dict, book: str, author: str, series: str
):
    """Save or merge a group file."""
    group_name = group.get("group_name", "Unknown")
    group_id = group.get("GroupID", str(new_ulid()))

    # Add book metadata to event mentions
    for mention in group.get("event_mentions", []):
        mention["book"] = book
        mention["author"] = author
        mention["series"] = series

    # Check if group already exists
    normalized = _normalize_name(group_name)
    existing_filename = None
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
            existing_filename = index.get(normalized)

    if existing_filename and (groups_dir / existing_filename).exists():
        # Merge with existing
        from src.utils.file_lock import locked_json

        with locked_json(groups_dir / existing_filename) as (existing_group, save):
            merged = _merge_group(existing_group, group)
            save(merged)
        logger.info("    Updated: %s", group_name)
    else:
        # Create new file
        filename = _name_to_filename(group_name, group_id)
        filepath = groups_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(group, f, indent=2, ensure_ascii=False)
        _update_index(index_file, group_name, filename)
        logger.info("    Created: %s", group_name)


def extract_people_groups(
    event_file: Path, grok_client: GrokClient, output_dir: Path
) -> Optional[Path]:
    """Extract people groups from event file."""
    groups_dir = output_dir / "people_groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    if _is_already_processed(groups_dir, event_file):
        logger.info(
            "Event already processed for people groups extraction: %s", event_file.name
        )
        return groups_dir

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    book = event_data.get("book", "Unknown")
    author = event_data.get("author", "Unknown")
    series = event_data.get("series", "Unknown")

    logger.info("  Extracting people groups from %s...", event_file.name)
    prompt = _build_extraction_prompt(event_data)
    response = grok_client.chat_completion(
        prompt, temperature=0.3, cache_type="people_groups"
    )

    if not response:
        logger.error("  Failed to get response from Grok API")
        return None

    try:
        groups = _parse_groups_response(response)
    except json.JSONDecodeError:
        logger.warning("  No valid JSON in response (likely no groups in this section)")
        logger.debug("  Response content: %s", response[:500])
        return groups_dir

    if not groups:
        logger.info("  No people groups found")
        return groups_dir

    groups = _fix_invalid_ulids(groups)
    groups = _validate_group_items(groups)

    index_file = groups_dir / "index.json"
    for group in groups:
        _save_group(groups_dir, index_file, group, book, author, series)

    _mark_as_processed(
        groups_dir, event_file, len(groups), event_data.get("extracted_date", "")
    )
    return groups_dir
