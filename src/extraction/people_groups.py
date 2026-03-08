#!/usr/bin/env python3
"""
Extract people groups (military units, countries, alliances, organizations) from events.

Similar to people extraction, creates individual files per group in output/people_groups/
with central index for lookups.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional

from ulid import new as new_ulid

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize group name for index lookup."""
    return name.lower().strip()


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


def _merge_group(existing: Dict, new_group: Dict) -> Dict:
    """
    Merge new group data into existing group.

    - Combines event mentions (deduplicates by MentionID)
    - Updates description if more detailed
    - Merges member lists
    - Merges members (deduplicates by PersonID)
    """
    merged = existing.copy()

    # Merge event mentions
    existing_mentions = merged.get("event_mentions", [])
    new_mentions = new_group.get("event_mentions", [])

    mention_ids = {m["MentionID"] for m in existing_mentions}
    for mention in new_mentions:
        if mention["MentionID"] not in mention_ids:
            existing_mentions.append(mention)

    merged["event_mentions"] = existing_mentions

    # Update description if new one is longer/more detailed
    if len(new_group.get("description", "")) > len(existing.get("description", "")):
        merged["description"] = new_group["description"]

    # Merge member lists (for alliances, sub-organizations)
    if "member_countries" in new_group:
        existing_members = set(merged.get("member_countries", []))
        new_members = set(new_group.get("member_countries", []))
        merged["member_countries"] = sorted(existing_members | new_members)

    if "sub_organizations" in new_group:
        existing_subs = set(merged.get("sub_organizations", []))
        new_subs = set(new_group.get("sub_organizations", []))
        merged["sub_organizations"] = sorted(existing_subs | new_subs)

    # Merge members (people associated with group)
    if "members" in new_group:
        existing_people = merged.get("members", [])
        new_people = new_group.get("members", [])

        # Deduplicate by PersonID
        person_ids = {p.get("PersonID") for p in existing_people if p.get("PersonID")}
        for person in new_people:
            if person.get("PersonID") and person["PersonID"] not in person_ids:
                existing_people.append(person)

        merged["members"] = existing_people

    return merged


def extract_people_groups(
    event_file: Path, grok_client: GrokClient, output_dir: Path
) -> Optional[Path]:
    """
    Extract people groups from event file.

    Creates/updates individual group files in output_dir/people_groups/
    Returns the people_groups directory path.
    """
    # Create people_groups directory
    groups_dir = output_dir / "people_groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    # Check if this event file has already been processed
    processed_registry = groups_dir / ".processed_events.json"
    event_file_str = str(event_file.resolve())

    if processed_registry.exists():
        with open(processed_registry, "r", encoding="utf-8") as f:
            processed = json.load(f)
        if event_file_str in processed:
            logger.info(
                f"Event already processed for people groups extraction: {event_file.name}"
            )
            return groups_dir

    index_file = groups_dir / "index.json"

    # Load event data
    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    book = event_data.get("book", "Unknown")
    author = event_data.get("author", "Unknown")
    series = event_data.get("series", "Unknown")

    # Build prompt
    prompt = f"""Extract all people groups mentioned in these events.

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

    # Call API
    logger.info("  Extracting people groups from %s...", event_file.name)
    response = grok_client.chat_completion(
        prompt, temperature=0.3, cache_type="people_groups"
    )

    if not response:
        logger.error("  Failed to get response from Grok API")
        return None

    # Parse response
    try:
        # Try to extract JSON if wrapped in markdown code blocks
        response_clean = response.strip()
        if response_clean.startswith("```"):
            # Extract JSON from code block
            match = re.search(
                r"```(?:json)?\s*(\{.*\})\s*```", response_clean, re.DOTALL
            )
            if match:
                response_clean = match.group(1)

        data = json.loads(response_clean)
        groups = data.get("People_Groups", [])
    except json.JSONDecodeError as e:
        logger.warning("  No valid JSON in response (likely no groups in this section)")
        logger.debug(f"  Response content: {response[:500]}")
        return groups_dir  # Return successfully with no groups

    if not groups:
        logger.info("  No people groups found")
        return groups_dir

    # Process each group
    for group in groups:
        group_name = group.get("group_name", "Unknown")
        group_id = group.get("GroupID", str(new_ulid()))

        # Add book metadata to event mentions
        for mention in group.get("event_mentions", []):
            mention["book"] = book
            mention["author"] = author
            mention["series"] = series

        # Check if group already exists in index
        normalized = _normalize_name(group_name)
        existing_filename = None

        if index_file.exists():
            with open(index_file, "r", encoding="utf-8") as f:
                index = json.load(f)
                existing_filename = index.get(normalized)

        if existing_filename and (groups_dir / existing_filename).exists():
            # Merge with existing
            with open(groups_dir / existing_filename, "r", encoding="utf-8") as f:
                existing_group = json.load(f)

            merged = _merge_group(existing_group, group)

            with open(groups_dir / existing_filename, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)

            logger.info("    Updated: %s", group_name)
        else:
            # Create new file
            filename = _name_to_filename(group_name, group_id)
            filepath = groups_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(group, f, indent=2, ensure_ascii=False)

            _update_index(index_file, group_name, filename)
            logger.info("    Created: %s", group_name)

    # Mark this event file as processed
    if processed_registry.exists():
        with open(processed_registry, "r", encoding="utf-8") as f:
            processed = json.load(f)
    else:
        processed = {}

    processed[event_file_str] = {
        "processed_at": event_data.get("extracted_date", ""),
        "groups_extracted": len(groups),
    }

    with open(processed_registry, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2)

    return groups_dir
