#!/usr/bin/env python3
"""Re-enrich existing equipment files with Wikipedia/Grokipedia data."""

import json
import logging
from pathlib import Path

from src.grok_client import GrokClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def enrich_equipment_data(common_name, technical_identifier, category, grok_client):
    """Enrich equipment data with external sources."""
    identifier = technical_identifier or common_name

    prompt = f"""Look up information about this WWII military equipment: {identifier} ({common_name})
Category: {category}

Provide a brief summary with:
1. Description (2-3 sentences)
2. Key specifications (if applicable: weight, dimensions, armament, speed, range, crew)
3. Alternate names/designations
4. Notable variants

Return as JSON:
{{
  "description": "Brief description",
  "specifications": {{"key": "value"}},
  "alternate_names": ["name1", "name2"],
  "variants": [{{"variant_name": "name", "description": "desc"}}]
}}

If information is not available, return empty fields."""

    try:
        response = grok_client.chat_completion(
            prompt, temperature=0.1, use_cache=True, cache_type="equipment_enrichment"
        )
        return json.loads(response)
    except Exception as e:
        logger.error(f"Failed to enrich {common_name}: {e}")
        return {}


def main():
    equipment_dir = Path("output/equipment")
    if not equipment_dir.exists():
        logger.error("Equipment directory not found")
        return

    grok_client = GrokClient()
    enriched_count = 0

    for eq_file in equipment_dir.glob("*.json"):
        if eq_file.name in ["index.json", ".processed_events.json"]:
            continue

        with open(eq_file) as f:
            eq_data = json.load(f)

        # Skip if already has description
        if eq_data.get("description"):
            logger.info(f"Skipping {eq_data['common_name']} (already enriched)")
            continue

        logger.info(f"Enriching {eq_data['common_name']}...")
        enriched = enrich_equipment_data(
            eq_data["common_name"],
            eq_data.get("technical_identifier"),
            eq_data["category"],
            grok_client,
        )

        # Merge enriched data
        updated = False
        for key in ["description", "specifications", "alternate_names", "variants"]:
            if key in enriched and enriched[key] and not eq_data.get(key):
                eq_data[key] = enriched[key]
                updated = True
                logger.info(f"  Added {key}")

        if updated:
            with open(eq_file, "w") as f:
                json.dump(eq_data, f, indent=2)
            enriched_count += 1

    logger.info(f"\nEnriched {enriched_count} equipment files")


if __name__ == "__main__":
    main()
