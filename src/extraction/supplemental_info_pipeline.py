"""Extract entities from supplemental information (non-referenced material)."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def extract_from_supplemental_info(
    material: Dict[str, Any],
    event_id: str,
    sub_event_id: str,
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
) -> Dict[str, List[Path]]:
    """
    Extract entities from supplemental information material.

    Routes through standard extraction pipeline for:
    - casualties, dates, equipment, logistics, maps, people, people_groups, places, weather

    Returns:
        dict: Extracted files by type
    """
    verbatim = material.get("verbatim_reference", "")
    material_id = material.get("MaterialID", "")

    if not verbatim:
        return {}

    logger.info("Extracting entities from supplemental info: %s", material_id[:8])

    # Create pseudo-event structure for extraction
    pseudo_event = {
        "Event": {
            "EventID": event_id,
            "Event_Name": f"Supplemental Material {material_id[:8]}",
            "Sub-events": [
                {
                    "Sub-eventID": sub_event_id,
                    "Sub-event_summary": f"Supplemental information from {material.get('reference_type', 'note')} {material.get('reference_number', '')}",
                    "Sub-event_fulltext": {"1": verbatim},
                    "source_material_id": material_id,
                }
            ],
        }
    }

    extracted_files = {}

    # Extract dates
    if config.get("dates", {}).get("enabled", True):
        try:
            from src.extraction.dates import extract_dates

            dates_file = extract_dates(pseudo_event, grok_client, output_root / "dates")
            if dates_file:
                extracted_files.setdefault("dates", []).append(dates_file)
        except Exception as e:
            logger.error("Failed to extract dates from supplemental: %s", e)

    # Extract places
    if config.get("places", {}).get("enabled", True):
        try:
            from src.extraction.places import extract_places

            places_file = extract_places(
                pseudo_event, grok_client, output_root / "places"
            )
            if places_file:
                extracted_files.setdefault("places", []).append(places_file)
        except Exception as e:
            logger.error("Failed to extract places from supplemental: %s", e)

    # Extract people
    if config.get("people", {}).get("enabled", True):
        try:
            from src.extraction.people import extract_people

            people_files = extract_people(
                pseudo_event, grok_client, output_root / "people"
            )
            if people_files:
                extracted_files["people"] = people_files
        except Exception as e:
            logger.error("Failed to extract people from supplemental: %s", e)

    # Extract people groups
    if config.get("people_groups", {}).get("enabled", True):
        try:
            from src.extraction.people_groups import extract_people_groups

            groups_files = extract_people_groups(
                pseudo_event, grok_client, output_root / "people_groups"
            )
            if groups_files:
                extracted_files["people_groups"] = groups_files
        except Exception as e:
            logger.error("Failed to extract people groups from supplemental: %s", e)

    # Extract equipment
    if config.get("equipment", {}).get("enabled", False):
        try:
            from src.extraction.equipment import extract_equipment

            equipment_files = extract_equipment(
                pseudo_event, grok_client, output_root / "equipment"
            )
            if equipment_files:
                extracted_files["equipment"] = equipment_files
        except Exception as e:
            logger.error("Failed to extract equipment from supplemental: %s", e)

    logger.info(
        "Extracted %d entity types from supplemental info", len(extracted_files)
    )
    return extracted_files


def process_supplemental_information(
    supplemental_file: Path,
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
) -> int:
    """
    Process all supplemental information in a file.

    Returns:
        int: Number of supplemental info materials processed
    """
    try:
        with open(supplemental_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error("Failed to load %s: %s", supplemental_file, e)
        return 0

    processed = 0

    for entry in data:
        event_id = entry.get("EventID", "")
        sub_event_id = entry.get("Sub-eventID", "")

        for material in entry.get("Supplemental_Material", []):
            category = material.get("material_category", "referenced_material")

            if category == "supplemental_information":
                extract_from_supplemental_info(
                    material, event_id, sub_event_id, grok_client, output_root, config
                )
                processed += 1

    logger.info(
        "Processed %d supplemental information materials from %s",
        processed,
        supplemental_file.name,
    )
    return processed
