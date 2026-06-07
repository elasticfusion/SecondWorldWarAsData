"""Equipment dedup operations — extracted from equipment.py for readability."""

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import requests
import ulid
from datetime import datetime, timezone
from difflib import SequenceMatcher

if TYPE_CHECKING:
    from src.grok_client import GrokClient

from src.extraction.equipment_ext.media import _enrich_and_add_media

logger = logging.getLogger(__name__)


def _fuzzy_match_equipment(
    name: str, equipment_index: Dict[str, Path], threshold: float = 0.80
) -> Optional[str]:
    """Find best fuzzy match for equipment name.

    Checks both common_name and alternate_names from equipment files.

    Args:
        name: Equipment name to match
        equipment_index: Index of existing equipment
        threshold: Minimum similarity ratio (0.0-1.0), default 0.80

    Returns:
        Matched equipment name or None
    """
    if not equipment_index:
        return None

    best_match = None
    best_ratio = 0.0

    name_lower = name.lower()

    # Check common names
    for existing_name in equipment_index.keys():
        ratio = SequenceMatcher(None, name_lower, existing_name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = existing_name

    # Also check alternate names in files
    for existing_name, eq_file in equipment_index.items():
        try:
            with open(eq_file, encoding="utf-8") as f:
                eq_data = json.load(f)
                for alt_name in eq_data.get("alternate_names", []):
                    ratio = SequenceMatcher(None, name_lower, alt_name.lower()).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = existing_name
        except Exception:  # nosec B112
            continue  # Skip invalid entries

    if best_ratio >= threshold:
        logger.debug("Fuzzy matched '%s' to '%s' (%.2f)", name, best_match, best_ratio)
        return best_match

    return None


def _find_matching_equipment(
    common_name: str,
    equipment_index: Dict[str, Path],
    technical_id: str = "",
) -> Optional[str]:
    """Find matching equipment by exact or fuzzy match."""
    # Check technical_identifier first (most stable)
    if technical_id and technical_id in equipment_index:
        return technical_id
    if common_name in equipment_index:
        return common_name
    return _fuzzy_match_equipment(common_name, equipment_index)


def _merge_equipment_fields(existing: dict, equipment_data: dict) -> None:
    """Merge equipment fields from new data into existing."""
    for key in [
        "description",
        "alternate_names",
        "subcategory",
        "variants",
        "specifications",
    ]:
        if key not in equipment_data or not equipment_data[key]:
            continue

        if key == "alternate_names" and key in existing:
            # Merge alternate names
            existing[key] = list(set(existing[key] + equipment_data[key]))
        elif key == "variants" and key in existing:
            # Merge variants by variant_name
            existing_variants = {
                v["variant_name"]: v for v in existing.get("variants", [])
            }
            for new_variant in equipment_data.get("variants", []):
                existing_variants[new_variant["variant_name"]] = new_variant
            existing["variants"] = list(existing_variants.values())
        else:
            existing[key] = equipment_data[key]


def _merge_into_existing(
    eq_file: Path, new_mention: dict, equipment_data: dict, matched_name: str
) -> Path:
    """Merge mention into existing equipment file."""
    logger.debug("Merging mention into existing equipment: %s", matched_name)

    # Load existing
    from src.utils.file_lock import locked_json

    with locked_json(eq_file) as (existing, save):
        # Check if mention already exists (semantic dedup by event+sub-event)
        existing_keys = {
            (m.get("EventID"), m.get("Sub_eventID"))
            for m in existing.get("event_mentions", [])
        }
        new_key = (new_mention.get("EventID"), new_mention.get("Sub_eventID"))
        if new_key in existing_keys:
            logger.debug("Mention for %s already exists, skipping", new_key)
            return eq_file

        # Append mention
        existing["event_mentions"].append(new_mention)

        # Update optional fields
        _merge_equipment_fields(existing, equipment_data)

        # Save
        save(existing)

    return eq_file


def _create_new_equipment(
    equipment_data: dict,
    new_mention: dict,
    equipment_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: Optional[GrokClient],
    enable_enrichment: bool,
    verify_media_with_vision: bool,
    dates_index: Optional[Dict[str, Dict[str, str]]],
) -> Path:
    """Create new equipment file."""
    common_name = equipment_data["common_name"]
    logger.debug("Creating new equipment file: %s", common_name)

    # Enrich with external data if enabled
    if enable_enrichment and grok_client:
        sub_event_id = new_mention.get("Sub_eventID")
        _enrich_and_add_media(
            equipment_data,
            common_name,
            grok_client,
            verify_media_with_vision,
            sub_event_id,
            dates_index,
        )

    equipment_id = str(ulid.new())
    equipment_data["EquipmentID"] = equipment_id
    equipment_data["event_mentions"] = [new_mention]
    equipment_data["extracted_date"] = datetime.now(timezone.utc).isoformat()

    safe_name = common_name.replace(" ", "_").replace("/", "_")
    eq_file = equipment_dir / f"{safe_name}_{equipment_id[:8]}.json"

    with open(eq_file, "w") as f:
        json.dump(equipment_data, f, indent=2)

    # Update index (prefer technical_identifier for stability)
    index_key = equipment_data.get("technical_identifier") or common_name
    equipment_index[index_key] = eq_file
    # Also index by common_name for lookup compatibility
    if index_key != common_name:
        equipment_index[common_name] = eq_file

    return eq_file


def merge_or_create_equipment(
    equipment_data: dict,
    new_mention: dict,
    equipment_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: Optional[GrokClient] = None,
    enable_enrichment: bool = False,
    verify_media_with_vision: bool = True,
    dates_index: Optional[Dict[str, Dict[str, str]]] = None,
) -> Path:
    """Merge mention into existing equipment or create new file.

    Args:
        equipment_data: Equipment data (common_name, category, etc.)
        new_mention: New mention to add
        equipment_dir: Output directory
        equipment_index: Index of existing equipment
        grok_client: Grok API client for enrichment
        enable_enrichment: Whether to enrich new equipment with external data
        verify_media_with_vision: Verify media relevance with Grok vision API
        dates_index: Index of dates by Sub-eventID for temporal filtering

    Returns:
        Path to equipment file
    """
    common_name = equipment_data["common_name"]

    # Find matching equipment
    technical_id = equipment_data.get("technical_identifier", "")
    matched_name = _find_matching_equipment(common_name, equipment_index, technical_id)

    if matched_name:
        eq_file = equipment_index[matched_name]
        return _merge_into_existing(eq_file, new_mention, equipment_data, matched_name)
    else:
        return _create_new_equipment(
            equipment_data,
            new_mention,
            equipment_dir,
            equipment_index,
            grok_client,
            enable_enrichment,
            verify_media_with_vision,
            dates_index,
        )
