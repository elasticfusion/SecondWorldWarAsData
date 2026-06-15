"""Consolidate people entries that refer to the same individual."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def create_consolidation_prompt(people_list: List[Dict[str, Any]]) -> str:
    """Create prompt to identify duplicate people entries."""
    # Create simplified list for AI analysis
    people_summary = []
    for i, person in enumerate(people_list):
        bio = person.get("biographical_profile", {})
        mentions = person.get("event_mentions", [])

        summary = {
            "index": i,
            "name": person.get("name"),
            "birth_date": bio.get("birth_date") if bio else None,
            "death_date": bio.get("death_date") if bio else None,
            "nationality": bio.get("nationality") if bio else None,
            "role_type": bio.get("role_type") if bio else None,
            "positions": list(
                set(
                    m.get("position_at_event", "")
                    for m in mentions
                    if m.get("position_at_event")
                )
            ),
            "sample_text": mentions[0].get("original_text", "") if mentions else "",
        }
        people_summary.append(summary)

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt(
        "people_consolidation", entries=json.dumps(people_summary, indent=2)
    )

    return prompt


def _load_people_data(central_file: Path) -> list:
    """Load people data from central file."""
    with open(central_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("People", [])


def _get_duplicate_groups(people_list: list, grok_client: GrokClient) -> list:
    """Get duplicate groups from AI analysis."""
    prompt = create_consolidation_prompt(people_list)

    response = grok_client.chat_completion(
        prompt=prompt,
        system_prompt="You are an expert historian identifying duplicate person entries.",
        use_cache=False,
        cache_type="people",
    )

    try:
        result = json.loads(response)
        return result.get("duplicates", [])
    except json.JSONDecodeError:
        logger.error("Failed to parse consolidation response")
        return []


def _merge_biographical_fields(base_bio: dict, source_bio: dict) -> None:
    """Merge biographical fields from source into base."""
    fields = [
        "birth_date",
        "birth_place",
        "death_date",
        "death_place",
        "nationality",
        "role_type",
        "biographical_details",
    ]

    for field in fields:
        if source_bio.get(field) and not base_bio.get(field):
            base_bio[field] = source_bio[field]


def _merge_person_group(people_list: list, indices: list, canonical_name: str) -> dict:
    """Merge multiple person entries into one."""
    base_person = people_list[indices[0]].copy()
    base_person["name"] = canonical_name

    all_mentions = []
    all_awards = []

    for idx in indices:
        person = people_list[idx]
        all_mentions.extend(person.get("event_mentions", []))

        bio = person.get("biographical_profile", {})
        if bio:
            all_awards.extend(bio.get("military_awards", []))

            # Merge biographical fields
            base_bio = base_person.setdefault("biographical_profile", {})
            _merge_biographical_fields(base_bio, bio)

    # Deduplicate awards
    award_set = {json.dumps(a, sort_keys=True) for a in all_awards}
    unique_awards = [json.loads(a) for a in award_set]

    if base_person.get("biographical_profile"):
        base_person["biographical_profile"]["military_awards"] = unique_awards

    base_person["event_mentions"] = all_mentions
    return base_person


def _merge_duplicates(people_list: list, duplicates: list) -> list:
    """Merge all duplicate groups and return consolidated list."""
    merged_people = []
    processed_indices = set()

    for dup_group in duplicates:
        indices = dup_group["indices"]
        canonical_name = dup_group["canonical_name"]
        reason = dup_group["reason"]

        logger.info(f"Merging: {canonical_name} ({len(indices)} entries)")
        logger.debug(f"  Reason: {reason}")

        merged_person = _merge_person_group(people_list, indices, canonical_name)
        merged_people.append(merged_person)
        processed_indices.update(indices)

    # Add non-duplicate entries
    for i, person in enumerate(people_list):
        if i not in processed_indices:
            merged_people.append(person)

    return merged_people


def _save_consolidated_data(output_file: Path, merged_people: list) -> None:
    """Save consolidated people data to file."""
    output_data = {"People": merged_people}
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)


def consolidate_people(
    central_file: Path, grok_client: GrokClient, output_file: Path
) -> None:
    """
    Consolidate duplicate people entries in central file.

    Args:
        central_file: Path to people-central.json
        grok_client: Grok API client
        output_file: Path to save consolidated output
    """
    logger.info("Loading central people file...")
    people_list = _load_people_data(central_file)
    logger.info(f"Found {len(people_list)} people entries")

    if len(people_list) < 2:
        logger.info("Not enough entries to consolidate")
        return

    # Get consolidation suggestions from AI
    logger.info("Analyzing for duplicates...")
    duplicates = _get_duplicate_groups(people_list, grok_client)

    if not duplicates:
        return

    logger.info(f"Found {len(duplicates)} duplicate groups")

    # Merge duplicates
    merged_people = _merge_duplicates(people_list, duplicates)
    logger.info(f"Consolidated {len(people_list)} → {len(merged_people)} people")

    # Save consolidated file
    _save_consolidated_data(output_file, merged_people)
    logger.info(f"Saved consolidated file: {output_file}")
