"""Extract entities from supplemental information (non-referenced material)."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def _create_pseudo_event(
    material: Dict[str, Any], event_id: str, sub_event_id: str
) -> dict:
    """Create pseudo-event structure for extraction."""
    material_id = material.get("MaterialID", "")
    verbatim = material.get("verbatim_reference", "")

    return {
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


def _dispatch_extractor(
    entity_type: str,
    event_file: Path,
    grok_client: GrokClient,
    output_root: Path,
) -> Optional[Any]:
    """Route a single entity type to its extractor. Returns the extractor result or None."""
    result: Optional[Any] = None
    if entity_type == "dates":
        from src.extraction.dates import extract_dates

        result = extract_dates(event_file, grok_client, output_root / "dates")
    elif entity_type == "places":
        from src.extraction.places import extract_places

        result = extract_places(event_file, grok_client, output_root / "places")
    elif entity_type == "people":
        from src.extraction.people import extract_people

        result = extract_people(event_file, grok_client, output_root / "people")
    elif entity_type == "people_groups":
        from src.extraction.people_groups import extract_people_groups

        result = extract_people_groups(
            event_file, grok_client, output_root / "people_groups"
        )
    elif entity_type == "equipment":
        from src.extraction.equipment import extract_equipment_from_event

        # NB: equipment's signature is (event_file, output_dir, grok_client) — different arg
        # ORDER than dates/places/people. The old dead code passed them in the wrong order.
        result = extract_equipment_from_event(
            event_file, output_root / "equipment", grok_client
        )
    return result


def _extract_entity_type(
    entity_type: str,
    pseudo_event: dict,
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
) -> Optional[Any]:
    """Extract a single entity type from pseudo-event."""
    config_key = entity_type
    enabled = config.get(config_key, {}).get(
        "enabled", entity_type in ["dates", "places", "people", "people_groups"]
    )

    if not enabled:
        return None

    import json as _json
    import tempfile

    # The extractors take a PATH to an *-event.json (they open() it), NOT a dict. Passing the
    # pseudo_event dict raised TypeError that the broad except swallowed -> this whole supplemental
    # entity-extraction path produced 0 every run (PATTERN 4, 100% dead). Materialize the dict to a
    # temp event.json and pass its Path.
    tmp_dir = output_root / "_supplemental_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    _seid_raw = (pseudo_event.get("Event", {}).get("Sub-events") or [{}])[0].get(
        "Sub-eventID", "x"
    )
    # Sanitize before using as a tempfile PREFIX: mkstemp does NOT strip "/" or ".." from the
    # prefix, and Sub-eventID is read raw from (LLM/source) JSON. Keep only a safe alnum token.
    import re as _re

    seid = _re.sub(r"[^A-Za-z0-9]", "", str(_seid_raw))[:12] or "x"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(tmp_dir), prefix=f"{seid}_", suffix="-event.json"
    )
    event_file = Path(tmp_name)
    try:
        import os as _os

        with _os.fdopen(fd, "w", encoding="utf-8") as fh:
            _json.dump(pseudo_event, fh, ensure_ascii=False)
        result = _dispatch_extractor(entity_type, event_file, grok_client, output_root)
        logger.info(
            "OBSERV supplemental_info.extract type=%s result=%s",
            entity_type,
            "ok" if result else "none",
        )
        return result
    except Exception as e:
        logger.error("Failed to extract %s from supplemental: %s", entity_type, e)
        return None
    finally:
        event_file.unlink(missing_ok=True)


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

    # Create pseudo-event structure
    pseudo_event = _create_pseudo_event(material, event_id, sub_event_id)

    extracted_files: Dict[str, List[Path]] = {}

    # Extract all entity types
    entity_types = ["dates", "places", "people", "people_groups", "equipment"]

    for entity_type in entity_types:
        result = _extract_entity_type(
            entity_type, pseudo_event, grok_client, output_root, config
        )
        if result:
            if entity_type in ["people", "people_groups", "equipment"]:
                extracted_files[entity_type] = result  # type: ignore[assignment]
            else:
                extracted_files.setdefault(entity_type, []).append(result)

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
        # Normalize plural key variant from legacy data
        if "Supplemental_Materials" in entry and "Supplemental_Material" not in entry:
            entry["Supplemental_Material"] = entry.pop("Supplemental_Materials")

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
