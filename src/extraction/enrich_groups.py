"""Enrich people_groups with external data (Wikipedia/Grokipedia)."""

import json
import logging
from pathlib import Path
from typing import Optional

from src.grok_client import GrokClient
from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)

SKIP_FILES = frozenset(
    [
        "index.json",
        "not_duplicates.json",
        "duplicate_report.json",
        "related_groups_report.json",
        ".processed_events.json",
    ]
)

PROMPT = """Look up this WWII military unit/organization: {name}

Return JSON with:
- full_name: Official full designation (e.g., "101st Airborne Division (United States)")
- unit_type: division, corps, army, army_group, brigade, regiment, battalion, or other
- nationality: ISO 3166-1 alpha-3 country code
- branch: army, navy, air_force, marines, ss, or other
- parent_unit: Higher formation (e.g., "VII Corps" for a division)
- sub_organizations: Array of major subordinate units (e.g., ["Heer (Army)", "Luftwaffe (Air Force)"]). null if none or unknown
- member_countries: Array of member countries for alliances (e.g., ["Germany", "Italy", "Japan"]). null if not an alliance
- commanding_officers: Array of {{"name": "...", "from_date": "YYYY-MM", "to_date": "YYYY-MM"}} for WWII period only
- notable_operations: Array of operation/battle names this unit participated in
- formed_date: When unit was formed (YYYY or YYYY-MM)
- disbanded_date: When unit was disbanded (YYYY or YYYY-MM, null if still active)
- description: 1-2 sentence summary of the unit's WWII role

CRITICAL: Only include facts you are confident about. Use null for unknown fields.
Return ONLY valid JSON, no markdown."""


_ALLIANCE_MAP = {
    "USA": "Allied Powers",
    "US": "Allied Powers",
    "GBR": "Allied Powers",
    "CAN": "Allied Powers",
    "FRA": "Allied Powers",
    "POL": "Allied Powers",
    "SUN": "Allied Powers",
    "DEU": "Axis Powers",
    "ITA": "Axis Powers",
    "JPN": "Axis Powers",
}


def _promote_enrichment(data):
    """Promote enrichment_data fields to spec-level top-level fields."""
    enrich = data.get("enrichment_data")
    if not enrich or not isinstance(enrich, dict):
        return

    mapping = {
        "group_type": enrich.get("unit_type"),
        "country_of_origin": enrich.get("nationality"),
        "description": enrich.get("description"),
        "military_hierarchy": enrich.get("unit_type"),
        "parent_organization": enrich.get("parent_unit"),
        "common_name": enrich.get("full_name"),
        "sub_organizations": enrich.get("sub_organizations"),
        "member_countries": enrich.get("member_countries"),
    }

    for key, value in mapping.items():
        if value and not data.get(key):
            data[key] = value

    # Infer alliance from nationality
    nat = enrich.get("nationality", "")
    if nat and not data.get("alliance_membership"):
        alliance = _ALLIANCE_MAP.get(nat)
        if alliance:
            data["alliance_membership"] = [alliance]

    # Source language is always English for this corpus
    if not data.get("source_language"):
        data["source_language"] = "English"


def enrich_group(group_file: Path, grok_client: GrokClient) -> bool:
    """Enrich a single people_group file. Returns True if enriched."""
    try:
        with open(group_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.debug("Failed to load %s: %s", group_file.name, e)
        return False

    name = data.get("name", "")
    if not name:
        return False

    # Skip if already enriched
    if data.get("enrichment_data"):
        logger.debug("Already enriched: %s", name)
        return False

    logger.info("Enriching: %s", name)

    try:
        enrichment = grok_client.extract_json(
            prompt=PROMPT.format(name=name),
            use_cache=True,
            cache_type="group_enrichment",
        )
    except Exception as e:
        logger.warning("Failed to enrich %s: %s", name, e)
        return False

    if not isinstance(enrichment, dict):
        return False

    data["enrichment_data"] = enrichment
    # Ensure group_name exists per spec (alias of name)
    if not data.get("group_name") and data.get("name"):
        data["group_name"] = data["name"]
    _promote_enrichment(data)
    write_json_with_lock(group_file, data)
    logger.info("  ✓ Enriched %s", name)
    return True


def enrich_all_groups(
    groups_dir: Path,
    grok_client: GrokClient,
    max_groups: Optional[int] = None,
) -> int:
    """Enrich all people_groups in directory.

    Returns number of groups enriched.
    """
    if not groups_dir.exists():
        logger.error("Directory not found: %s", groups_dir)
        return 0

    group_files = [f for f in groups_dir.glob("*.json") if f.name not in SKIP_FILES]

    if max_groups:
        group_files = group_files[:max_groups]

    logger.info("Enriching %d people groups...", len(group_files))
    logger.info("=" * 60)

    enriched = 0
    for group_file in group_files:
        if enrich_group(group_file, grok_client):
            enriched += 1

    logger.info("=" * 60)
    logger.info("Group enrichment complete: %d/%d enriched", enriched, len(group_files))
    return enriched
