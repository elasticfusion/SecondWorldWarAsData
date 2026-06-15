"""Equipment enrichment operations — extracted from equipment.py for readability."""

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def _extract_year_from_date(
    sub_event_id: Optional[str], dates_index: Optional[Dict[str, Dict[str, str]]]
) -> Optional[str]:
    """Extract year from date index."""
    if not sub_event_id or not dates_index or sub_event_id not in dates_index:
        return None

    date_info = dates_index[sub_event_id]
    date_str = date_info.get("date_start", "")

    if date_str and len(date_str) >= 4:
        return date_str[:4]  # Extract year (YYYY)

    return None


def _build_external_data(
    enriched: Dict[str, Any], equipment_data: Dict[str, Any]
) -> None:
    """Build external_data from enrichment URLs if not already present."""
    wiki_url = enriched.get("wikipedia_url")
    grok_url = enriched.get("grokipedia_url")
    if (wiki_url or grok_url) and "external_data" not in equipment_data:
        ext: Dict[str, Any] = {}
        if grok_url:
            ext["grokipedia_url"] = grok_url
        if wiki_url:
            ext["wikipedia_url"] = wiki_url
        equipment_data["external_data"] = ext


def _merge_enriched_data(
    equipment_data: Dict[str, Any], enriched: Dict[str, Any]
) -> None:
    """Merge enriched data into equipment data (don't overwrite existing)."""
    for key in ["description", "specifications", "alternate_names", "variants"]:
        if key in enriched and enriched[key]:
            if key not in equipment_data or not equipment_data[key]:
                equipment_data[key] = enriched[key]
                logger.debug("  Enriched %s: %s", key, type(enriched[key]).__name__)
    _build_external_data(enriched, equipment_data)


def _enrich_equipment_data(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
) -> Dict[str, Any]:
    """Enrich equipment data with external sources (Wikipedia/Grokipedia).

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client

    Returns:
        Dict with enriched data (description, specifications, etc.)
    """
    identifier = technical_identifier or common_name

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt(
        "equipment_enrichment",
        identifier=identifier,
        common_name=common_name,
        category=category,
        country="",
    )

    try:
        response = grok_client.chat_completion(
            prompt,
            temperature=0.1,
            use_cache=True,
            cache_type="equipment_enrichment",
        )

        enriched = json.loads(response)
        logger.debug("Enriched data for %s", common_name)
        return enriched
    except Exception as e:
        logger.warning("Failed to enrich equipment data for %s: %s", common_name, e)
        return {}
