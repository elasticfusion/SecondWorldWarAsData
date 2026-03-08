"""
Biographical enrichment from external sources.

Searches Grokipedia and Wikipedia for additional biographical data
after person extraction. Follows references for deeper enrichment.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def search_grokipedia(
    person_name: str, timeout: int = 30, max_retries: int = 2
) -> Optional[str]:
    """Search Grokipedia for person biographical data."""
    for attempt in range(max_retries):
        try:
            search_url = f"https://grokipedia.com/search?q={person_name}"
            headers = {
                "User-Agent": "WWII-Data-Extraction-Bot/1.0 (Historical research project; contact via GitHub)"
            }

            response = requests.get(
                search_url, headers=headers, timeout=timeout, allow_redirects=True
            )

            if response.status_code == 200:
                return response.text

            logger.debug(
                f"Grokipedia returned {response.status_code} for {person_name}"
            )
            return None

        except requests.Timeout as e:
            if attempt < max_retries - 1:
                logger.debug(
                    f"Grokipedia timeout for {person_name}, retrying ({attempt + 2}/{max_retries})..."
                )
            else:
                logger.debug(f"Grokipedia timeout for {person_name}: {e}")
        except Exception as e:
            logger.debug(f"Grokipedia search failed for {person_name}: {e}")
            return None

    return None


def search_wikipedia(
    person_name: str, timeout: int = 30, max_retries: int = 2
) -> Optional[str]:
    """Search Wikipedia for person biographical data."""
    for attempt in range(max_retries):
        try:
            # Use Wikipedia API
            api_url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "format": "json",
                "titles": person_name,
                "prop": "extracts",
                "exintro": "True",
                "explaintext": "True",
            }
            headers = {
                "User-Agent": "WWII-Data-Extraction-Bot/1.0 (Historical research project; contact via GitHub)",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            }

            response = requests.get(
                api_url, params=params, headers=headers, timeout=timeout
            )

            if response.status_code == 200:
                data = response.json()
                pages = data.get("query", {}).get("pages", {})

                for page_id, page_data in pages.items():
                    if page_id != "-1":  # Page exists
                        return page_data.get("extract", "")

            if response.status_code == 403:
                logger.warning(
                    f"Wikipedia API blocked request for {person_name} (403 Forbidden). "
                    f"Wikipedia may be rate limiting or blocking automated requests. "
                    f"Consider using a different approach or contacting Wikipedia."
                )
                return None  # Don't retry 403s

            logger.debug(f"Wikipedia returned {response.status_code} for {person_name}")
            return None

        except requests.Timeout as e:
            if attempt < max_retries - 1:
                logger.debug(
                    f"Wikipedia timeout for {person_name}, retrying ({attempt + 2}/{max_retries})..."
                )
            else:
                logger.debug(f"Wikipedia timeout for {person_name}: {e}")
        except requests.HTTPError as e:
            if e.response.status_code == 403:
                logger.warning(
                    f"Wikipedia API blocked request for {person_name} (403 Forbidden). "
                    f"Wikipedia may be rate limiting or blocking automated requests."
                )
                return None  # Don't retry 403s
            logger.debug(f"Wikipedia HTTP error for {person_name}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Wikipedia search failed for {person_name}: {e}")
            return None

    return None


def extract_biographical_data(
    person_name: str,
    source_text: str,
    source_name: str,
    grok_client: GrokClient,
    max_retries: int = 2,
) -> Optional[Dict[str, Any]]:
    """Extract structured biographical data from source text using Grok."""

    prompt = f"""Extract biographical data for {person_name} from this text.

Source: {source_name}

Text:
{source_text[:5000]}

Return JSON with any available biographical data:
{{
  "birth_date": "YYYY-MM-DD or null",
  "birth_place": "Location or null",
  "death_date": "YYYY-MM-DD or null",
  "death_place": "Location or null",
  "nationality": "Nationality or null",
  "ranks": [
    {{"rank": "General", "date": "1943", "branch": "US Army"}}
  ],
  "units_served": [
    {{"unit": "XIX Army Corps", "from": "1938", "to": "1940"}}
  ],
  "education": [
    {{"institution": "West Point", "degree": "Bachelor of Science", "year": "1915"}}
  ],
  "military_awards": [
    {{"award": "Medal of Honor", "class": null, "date_awarded": "1945"}}
  ],
  "family": {{
    "spouse": "Name or null",
    "children": ["Child1", "Child2"]
  }},
  "aliases": ["Nickname1", "Nickname2"],
  "biographical_details": "Brief summary",
  "references": ["Unit name", "Organization name", "Related person"]
}}

Only include fields with actual data. Return empty object if no data found."""

    for attempt in range(max_retries):
        try:
            result = grok_client.extract_json(
                prompt=prompt,
                cache_type="api",
                temperature=0.1,
                use_cache=(attempt == 0),  # Use cache on first attempt only
            )

            if isinstance(result, dict) and result:
                # Add source tracking
                result["_source"] = source_name
                return result

            return None

        except Exception as e:
            if attempt < max_retries - 1:
                logger.debug(
                    f"Biographical extraction failed for {person_name}, retrying ({attempt + 2}/{max_retries})..."
                )
            else:
                logger.debug(f"Biographical extraction failed for {person_name}: {e}")

    return None


def search_references(
    references: list[str],
    grok_client: GrokClient,
    max_references: int = 3,
) -> list[Dict[str, Any]]:
    """Search for additional data from referenced entities."""
    enrichment_data = []
    searched = set()  # Track searched references

    for ref in references[:max_references]:
        # Skip if already searched
        ref_normalized = ref.lower().strip()
        if ref_normalized in searched:
            logger.debug(f"  Skipping duplicate reference: {ref}")
            continue

        searched.add(ref_normalized)
        logger.info(f"  Searching reference: {ref}")

        # Try Grokipedia first
        text = search_grokipedia(ref)
        if text:
            data = extract_biographical_data(
                ref, text, f"Grokipedia: {ref}", grok_client
            )
            if data:
                enrichment_data.append(data)
                continue

        # Try Wikipedia
        text = search_wikipedia(ref)
        if text:
            data = extract_biographical_data(
                ref, text, f"Wikipedia: {ref}", grok_client
            )
            if data:
                enrichment_data.append(data)

    return enrichment_data


def _search_and_enrich(
    person_name: str,
    source_name: str,
    search_func,
    bio_profile: Dict[str, Any],
    grok_client: GrokClient,
) -> bool:
    """Search source and enrich if data found. Returns True if enriched."""
    logger.info(f"  Searching {source_name}...")
    text = search_func(person_name)

    if text:
        data = extract_biographical_data(person_name, text, source_name, grok_client)
        if data:
            return _merge_enrichment(bio_profile, data)

    return False


def enrich_person_biography(
    person_file: Path,
    grok_client: GrokClient,
    search_references_flag: bool = True,
) -> bool:
    """Enrich person biography from external sources.

    Args:
        person_file: Path to person JSON file
        grok_client: Grok API client
        search_references_flag: Whether to follow references

    Returns:
        True if enrichment was added
    """
    try:
        with open(person_file, encoding="utf-8") as f:
            person_data = json.load(f)

        person_name = person_data.get("name", "")
        if not person_name:
            return False

        logger.info(f"Enriching: {person_name}")

        bio_profile = person_data.get("biographical_profile", {})
        enrichment_added = False

        # Search Grokipedia
        enrichment_added = _search_and_enrich(
            person_name, "Grokipedia", search_grokipedia, bio_profile, grok_client
        )

        # Search Wikipedia
        enrichment_added = (
            _search_and_enrich(
                person_name, "Wikipedia", search_wikipedia, bio_profile, grok_client
            )
            or enrichment_added
        )

        # Follow references if enabled
        if search_references_flag:
            references = bio_profile.get("references", [])
            if references:
                logger.info(f"  Following {len(references)} reference(s)...")
                ref_data = search_references(references, grok_client)
                for data in ref_data:
                    enrichment_added = (
                        _merge_enrichment(bio_profile, data) or enrichment_added
                    )

        # Save if enriched
        if enrichment_added:
            person_data["biographical_profile"] = bio_profile

            # Validate before saving
            try:
                from src.extraction.people import Person

                Person(**person_data)
            except Exception as e:
                logger.error(f"  ❌ Validation failed for {person_name}: {e}")
                return False

            with open(person_file, "w", encoding="utf-8") as f:
                json.dump(person_data, f, indent=2, ensure_ascii=False)
            logger.info(f"  ✅ Enriched {person_name}")
            return True
        else:
            logger.info(f"  No new data found")
            return False

    except Exception as e:
        logger.error(f"Enrichment failed for {person_file.name}: {e}")
        return False


def _merge_simple_fields(
    bio_profile: Dict[str, Any], enrichment: Dict[str, Any]
) -> bool:
    """Merge simple fields. Returns True if modified."""
    modified = False
    simple_fields = [
        "birth_date",
        "birth_place",
        "death_date",
        "death_place",
        "nationality",
        "role_type",
        "biographical_details",
    ]

    for field in simple_fields:
        if enrichment.get(field) and not bio_profile.get(field):
            bio_profile[field] = enrichment[field]
            modified = True

    return modified


def _merge_list_fields(bio_profile: Dict[str, Any], enrichment: Dict[str, Any]) -> bool:
    """Merge list fields without duplicates. Returns True if modified."""
    from src.extraction.people import (
        _deduplicate_awards,
        _deduplicate_ranks,
        _deduplicate_units,
        _merge_list_field,
    )

    modified = False

    # Map fields to their deduplication functions
    field_handlers = {
        "ranks": _deduplicate_ranks,
        "units_served": _deduplicate_units,
        "military_awards": _deduplicate_awards,
        "education": None,
        "aliases": None,
    }

    for field, dedupe_func in field_handlers.items():
        if enrichment.get(field):
            existing = bio_profile.get(field, [])
            merged = _merge_list_field(existing.copy(), enrichment[field], dedupe_func)
            if merged != existing:
                bio_profile[field] = merged
                modified = True

    return modified


def _merge_family(bio_profile: Dict[str, Any], enrichment: Dict[str, Any]) -> bool:
    """Merge family data. Returns True if modified."""
    modified = False

    if enrichment.get("family"):
        existing_family = bio_profile.get("family", {})
        new_family = enrichment["family"]

        if new_family.get("spouse") and not existing_family.get("spouse"):
            existing_family["spouse"] = new_family["spouse"]
            modified = True

        if new_family.get("children"):
            existing_children = existing_family.get("children", [])
            child_set = set(existing_children)
            for child in new_family["children"]:
                if child not in child_set:
                    existing_children.append(child)
                    modified = True
            existing_family["children"] = existing_children

        bio_profile["family"] = existing_family

    return modified


def _merge_enrichment(bio_profile: Dict[str, Any], enrichment: Dict[str, Any]) -> bool:
    """Merge enrichment data into biographical profile.

    Returns True if any new data was added.
    """
    source = enrichment.pop("_source", "Unknown")

    # Merge different field types
    modified = _merge_simple_fields(bio_profile, enrichment)
    modified = _merge_list_fields(bio_profile, enrichment) or modified
    modified = _merge_family(bio_profile, enrichment) or modified

    # Add source tracking if data was added
    if modified:
        sources = bio_profile.get("biography_sources", [])
        sources.append(
            {
                "source": source,
                "page": None,
                "confidence": 0.8,  # External sources get lower confidence
                "fields_sourced": list(enrichment.keys()),
            }
        )
        bio_profile["biography_sources"] = sources

    return modified


def enrich_all_people(
    people_dir: Path,
    grok_client: GrokClient,
    max_people: Optional[int] = None,
    search_references_flag: bool = True,
) -> int:
    """Enrich all people in directory from external sources.

    Args:
        people_dir: Directory containing person JSON files
        grok_client: Grok API client
        max_people: Maximum people to enrich (None for all)
        search_references_flag: Whether to follow references

    Returns:
        Number of people enriched
    """
    if not people_dir.exists():
        logger.error(f"Directory not found: {people_dir}")
        return 0

    person_files = [
        f
        for f in people_dir.glob("*.json")
        if f.name not in ["index.json", "duplicate_report.json", "not_duplicates.json"]
    ]

    if max_people:
        person_files = person_files[:max_people]

    logger.info(f"Enriching {len(person_files)} people from external sources...")
    logger.info("=" * 60)

    enriched = 0

    for person_file in person_files:
        if enrich_person_biography(person_file, grok_client, search_references_flag):
            enriched += 1

    logger.info("=" * 60)
    logger.info(f"Enrichment complete: {enriched}/{len(person_files)} people enriched")

    return enriched


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Enrich people biographies from external sources"
    )
    parser.add_argument("--max-people", type=int, help="Maximum people to enrich")
    parser.add_argument(
        "--no-references", action="store_true", help="Don't follow references"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    people_dir = Path("output/people")
    cache_dir = Path("cache/grok_cache")
    grok_client = GrokClient(cache_dir)

    enrich_all_people(
        people_dir,
        grok_client,
        max_people=args.max_people,
        search_references_flag=not args.no_references,
    )
