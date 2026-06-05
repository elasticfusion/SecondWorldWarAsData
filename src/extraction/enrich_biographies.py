"""
Biographical enrichment from external sources.

Searches Grokipedia and Wikipedia for additional biographical data
after person extraction. Follows references for deeper enrichment.
Validates source URLs by fetching content and verifying relevance via Grok.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from src.grok_client import BatchModeCollecting, GrokClient

logger = logging.getLogger(__name__)

_URL_HEADERS = {
    "User-Agent": "WWII-Data-Extraction-Bot/1.0 (Historical research project)"
}


def search_grokipedia(
    person_name: str, timeout: int = 30, max_retries: int = 2
) -> Optional[str]:
    """Search Grokipedia for person biographical data."""
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("grokipedia", person_name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

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
                cache_result("grokipedia", person_name, response.text)
                return response.text

            logger.debug(
                "Grokipedia returned %d for %s", response.status_code, person_name
            )
            cache_result("grokipedia", person_name, None)
            return None

        except requests.Timeout as e:
            if attempt < max_retries - 1:
                logger.debug(
                    "Grokipedia timeout for %s, retrying (%d/%d)...",
                    person_name,
                    attempt + 2,
                    max_retries,
                )
            else:
                logger.debug("Grokipedia timeout for %s: %s", person_name, e)
        except Exception as e:
            logger.debug("Grokipedia search failed for %s: %s", person_name, e)
            return None

    return None


def _build_wikipedia_request(person_name: str) -> tuple[str, dict, dict]:
    """Build Wikipedia API request parameters. Returns (url, params, headers)."""
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
    return api_url, params, headers


def _extract_page_content(response_data: dict) -> Optional[str]:
    """Extract page content from Wikipedia API response."""
    pages = response_data.get("query", {}).get("pages", {})

    for page_id, page_data in pages.items():
        if page_id != "-1":  # Page exists
            return page_data.get("extract", "")

    return None


def _handle_wikipedia_error(
    e: Exception, person_name: str, attempt: int, max_retries: int
) -> bool:
    """Handle Wikipedia API errors. Returns True if should retry."""
    if isinstance(e, requests.Timeout):
        if attempt < max_retries - 1:
            logger.debug(
                "Wikipedia timeout for %s, retrying (%d/%d)...",
                person_name,
                attempt + 2,
                max_retries,
            )
            return True
        logger.debug("Wikipedia timeout for %s: %s", person_name, e)
        return False

    if (
        isinstance(e, requests.HTTPError)
        and getattr(e, "response", None) is not None
        and e.response.status_code == 403
    ):
        logger.warning(
            "Wikipedia API blocked request for %s (403 Forbidden). "
            "Wikipedia may be rate limiting or blocking automated requests.",
            person_name,
        )
        return False

    logger.debug("Wikipedia search failed for %s: %s", person_name, e)
    return False


def search_wikipedia(
    person_name: str, timeout: int = 30, max_retries: int = 2
) -> Optional[str]:
    """Search Wikipedia for person biographical data."""
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("wikipedia", person_name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    api_url, params, headers = _build_wikipedia_request(person_name)

    for attempt in range(max_retries):
        try:
            response = requests.get(
                api_url, params=params, headers=headers, timeout=timeout
            )

            if response.status_code == 200:
                content = _extract_page_content(response.json())
                cache_result("wikipedia", person_name, content)
                return content

            if response.status_code == 403:
                logger.warning(
                    "Wikipedia API blocked request for %s (403 Forbidden). "
                    "Wikipedia may be rate limiting or blocking automated requests. "
                    "Consider using a different approach or contacting Wikipedia.",
                    person_name,
                )
                return None

            logger.debug(
                "Wikipedia returned %d for %s", response.status_code, person_name
            )
            return None

        except Exception as e:
            if not _handle_wikipedia_error(e, person_name, attempt, max_retries):
                return None

    cache_result("wikipedia", person_name, None)
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
  "role_type": "military_leader, political_leader, military_personnel, civilian, or null",
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
  "references": ["Unit name", "Organization name", "Related person"],
  "source_urls": ["https://en.wikipedia.org/wiki/...", "https://..."]
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

        except BatchModeCollecting:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                logger.debug(
                    f"Biographical extraction failed for {person_name}, retrying ({attempt + 2}/{max_retries})..."
                )
            else:
                logger.debug(
                    "Biographical extraction failed for %s: %s", person_name, e
                )

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
            logger.debug("  Skipping duplicate reference: %s", ref)
            continue

        searched.add(ref_normalized)
        logger.info("  Searching reference: %s", ref)

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


def _fetch_url_content(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch URL content. Returns text or None on failure."""
    try:
        resp = requests.get(
            url, headers=_URL_HEADERS, timeout=timeout, allow_redirects=True
        )
        if resp.status_code == 200:
            return resp.text
        logger.debug("  URL returned %d: %s", resp.status_code, url)
    except requests.RequestException as exc:
        logger.debug("  URL fetch failed (%s): %s", exc, url)
    return None


def _validate_url_relevance(
    url: str,
    page_text: str,
    person_name: str,
    grok_client: GrokClient,
) -> bool:
    """Ask Grok whether page content is relevant to the person."""
    prompt = (
        f"I fetched the following web page to verify biographical information "
        f'about the WWII-era person "{person_name}".\n\n'
        f"URL: {url}\n\n"
        f"Page content (first 3000 chars):\n{page_text[:3000]}\n\n"
        f'Is this page genuinely about "{person_name}" and does it contain '
        f"relevant biographical or military-service information?\n\n"
        f'Return JSON: {{"relevant": true/false, "reason": "brief explanation"}}'
    )
    try:
        result = grok_client.extract_json(
            prompt=prompt, cache_type="api", temperature=0.1
        )
        if isinstance(result, dict):
            return bool(result.get("relevant"))
    except Exception as exc:
        logger.debug("  URL relevance check failed: %s", exc)
    return False


def validate_source_urls(
    urls: list[str],
    person_name: str,
    grok_client: GrokClient,
    max_urls: int = 5,
) -> list[dict]:
    """Fetch each URL, verify it exists and is relevant via Grok.

    Returns list of validated source dicts with url, status, and reason.
    """
    validated = []
    for url in urls[:max_urls]:
        if not url or not url.startswith("http"):
            continue

        logger.info("  Validating URL: %s", url)

        page_text = _fetch_url_content(url)
        if page_text is None:
            logger.info("    ❌ URL unreachable")
            validated.append({"url": url, "status": "broken", "relevant": False})
            continue

        relevant = _validate_url_relevance(url, page_text, person_name, grok_client)
        status = "validated" if relevant else "irrelevant"
        logger.info("    %s %s", "✅" if relevant else "❌", status)
        validated.append({"url": url, "status": status, "relevant": relevant})

    return validated


def _search_and_enrich(
    person_name: str,
    source_name: str,
    search_func,
    bio_profile: Dict[str, Any],
    grok_client: GrokClient,
) -> bool:
    """Search source and enrich if data found. Returns True if enriched."""
    logger.info("  Searching %s...", source_name)
    text = search_func(person_name)

    if text:
        data = extract_biographical_data(person_name, text, source_name, grok_client)
        if data:
            return _merge_enrichment(bio_profile, data)

    return False


def _follow_references(bio_profile: Dict[str, Any], grok_client: GrokClient) -> bool:
    """Follow entity references and merge any enrichment data found."""
    references = bio_profile.get("references", [])
    if not references:
        return False
    logger.info("  Following %d reference(s)...", len(references))
    enriched = False
    for data in search_references(references, grok_client):
        enriched = _merge_enrichment(bio_profile, data) or enriched
    return enriched


def _validate_and_store_urls(
    bio_profile: Dict[str, Any],
    person_name: str,
    grok_client: GrokClient,
) -> bool:
    """Validate source URLs from Grok and store validated ones."""
    source_urls = bio_profile.pop("source_urls", [])
    if not source_urls:
        return False
    logger.info("  Validating %d source URL(s)...", len(source_urls))
    results = validate_source_urls(source_urls, person_name, grok_client)
    validated = [r for r in results if r["relevant"]]
    if not validated:
        return False
    sources = bio_profile.get("biography_sources", [])
    for r in validated:
        sources.append(
            {
                "source": r["url"],
                "page": None,
                "confidence": 0.9,
                "fields_sourced": ["url_validated"],
            }
        )
    bio_profile["biography_sources"] = sources
    return True


def _should_re_search(data: dict) -> bool:
    """Check if a not_found entity should be re-searched based on age."""
    from src.utils.config import load_config

    config = load_config()
    days = config.get("enrichment", {}).get("re_search_after_days", 90)
    last_search = data.get("last_enrichment_search")
    if not last_search:
        return True
    try:
        searched_date = datetime.strptime(last_search, "%Y-%m-%d")
        age = (datetime.now() - searched_date).days
        return age >= days
    except (ValueError, TypeError):
        return True


def _load_person_for_enrichment(person_file: Path) -> Optional[tuple]:
    """Load person file, return (data, name, bio_profile) or None if skip."""
    try:
        with open(person_file, encoding="utf-8") as f:
            person_data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to load %s: %s", person_file.name, e)
        return None

    person_name = person_data.get("name", "")
    if not person_name:
        return None

    bio_profile = person_data.get("biographical_profile", {})
    if bio_profile.get("birth_date") or bio_profile.get("biographical_details"):
        logger.debug("  Already enriched: %s, skipping", person_name)
        return None

    if person_data.get("enrichment_status") == "not_found":
        if not _should_re_search(person_data):
            logger.debug("  Previously searched, not found: %s, skipping", person_name)
            return None
        logger.info("  Re-searching (stale not_found): %s", person_name)

    return person_data, person_name, bio_profile


def _run_enrichment_steps(
    person_name: str,
    bio_profile: Dict[str, Any],
    grok_client: GrokClient,
    search_references_flag: bool,
) -> bool:
    """Run all enrichment steps, return True if any data added."""
    added = _search_and_enrich(
        person_name, "Grokipedia", search_grokipedia, bio_profile, grok_client
    )
    added = (
        _search_and_enrich(
            person_name, "Wikipedia", search_wikipedia, bio_profile, grok_client
        )
        or added
    )
    if search_references_flag:
        added = _follow_references(bio_profile, grok_client) or added
    added = _validate_and_store_urls(bio_profile, person_name, grok_client) or added
    return added


def enrich_person_biography(
    person_file: Path,
    grok_client: GrokClient,
    search_references_flag: bool = True,
) -> bool:
    """Enrich person biography from external sources."""

    loaded = _load_person_for_enrichment(person_file)
    if not loaded:
        return False

    person_data, person_name, bio_profile = loaded
    logger.info("Enriching: %s", person_name)

    try:
        enriched = _run_enrichment_steps(
            person_name, bio_profile, grok_client, search_references_flag
        )
    except BatchModeCollecting:
        # Batch mode — request collected, don't mark as not_found
        return False

    if not enriched:
        logger.info("  No new data found")
        person_data["enrichment_status"] = "not_found"
        person_data["last_enrichment_search"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d"
        )
        with open(person_file, "w", encoding="utf-8") as f:
            json.dump(person_data, f, indent=2, ensure_ascii=False)
        return False

    person_data["biographical_profile"] = bio_profile
    person_data["enrichment_status"] = "enriched"
    person_data["last_enrichment_search"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )
    try:
        from src.extraction.people import (
            Person,
        )  # pylint: disable=import-outside-toplevel

        Person(**person_data)
    except Exception as e:
        logger.error("  ❌ Validation failed for %s: %s", person_name, e)
        return False

    with open(person_file, "w", encoding="utf-8") as f:
        json.dump(person_data, f, indent=2, ensure_ascii=False)
    logger.info("  ✅ Enriched %s", person_name)
    return True


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


def _find_group_file(
    unit_name: str, group_index: Dict[str, str], groups_dir: Path
) -> Optional[Path]:
    """Find a people group file by unit name (case-insensitive)."""
    for key, filename in group_index.items():
        if key.lower() == unit_name.lower():
            path = groups_dir / filename
            return path if path.exists() else None
    return None


def _add_member_to_group(
    group_file: Path, person_id: str, person_name: str, unit: Dict[str, Any]
) -> bool:
    """Add person as member to a group file. Returns True if added."""
    with open(group_file, "r", encoding="utf-8") as f:
        group_data = json.load(f)

    members = group_data.setdefault("members", [])
    if any(m.get("PersonID") == person_id for m in members):
        return False

    members.append(
        {
            "PersonID": person_id,
            "name": person_name,
            "role": unit.get("role", "Member"),
            "from_date": unit.get("from"),
            "to_date": unit.get("to"),
            "source": "biographical_enrichment",
            "confidence": 0.8,
        }
    )

    with open(group_file, "w", encoding="utf-8") as f:
        json.dump(group_data, f, indent=2, ensure_ascii=False)
    return True


def _link_person_to_groups(person_file: Path, people_groups_dir: Path) -> int:
    """Add person as member to matching people groups based on units_served."""
    if not people_groups_dir.exists():
        return 0

    with open(person_file, "r", encoding="utf-8") as f:
        person_data = json.load(f)

    bio = person_data.get("biographical_profile", {})
    units = bio.get("units_served", [])
    if not units:
        return 0

    person_id = person_data.get("PersonID", "")
    person_name = person_data.get("name", "")
    if not person_name:
        return 0

    index_file = people_groups_dir / "index.json"
    if not index_file.exists():
        return 0
    with open(index_file, "r", encoding="utf-8") as f:
        group_index = json.load(f)

    linked = 0
    for unit in units:
        unit_name = unit.get("unit", "")
        if not unit_name:
            continue
        group_file = _find_group_file(unit_name, group_index, people_groups_dir)
        if not group_file:
            continue
        if _add_member_to_group(group_file, person_id, person_name, unit):
            linked += 1
            logger.info("  Linked %s → %s", person_name, unit_name)

    return linked


def enrich_all_people(
    people_dir: Path,
    grok_client: GrokClient,
    max_people: Optional[int] = None,
    search_references_flag: bool = True,
    max_workers: int = 6,
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
        logger.error("Directory not found: %s", people_dir)
        return 0

    person_files = [
        f
        for f in people_dir.glob("*.json")
        if f.name not in ["index.json", "duplicate_report.json", "not_duplicates.json"]
    ]

    if max_people:
        person_files = person_files[:max_people]

    logger.info("Enriching %d people from external sources...", len(person_files))

    from src.utils.heartbeat import Heartbeat

    heartbeat = Heartbeat(timeout=300, label="Phase 3")
    heartbeat.start()

    enriched = 0
    processed = 0
    total = len(person_files)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _enrich_one(pf):
        return enrich_person_biography(pf, grok_client, search_references_flag)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_enrich_one, pf): pf for pf in person_files}
        for future in as_completed(futures):
            pf = futures[future]
            processed += 1
            try:
                if future.result():
                    enriched += 1
            except Exception as e:
                logger.warning("Failed to enrich %s: %s", pf.stem, e)
            if processed % 10 == 0 or processed == total:
                logger.info(
                    "  People progress: %d/%d done (%d enriched)",
                    processed,
                    total,
                    enriched,
                )
            heartbeat.ping(f"{pf.stem} ({enriched} enriched)")

    heartbeat.stop()

    # Link enriched people to people groups
    people_groups_dir = people_dir.parent / "people_groups"
    if people_groups_dir.exists():
        logger.info("Linking people to groups...")
        groups_linked = 0
        for person_file in person_files:
            groups_linked += _link_person_to_groups(person_file, people_groups_dir)
        if groups_linked:
            logger.info("Linked %d person-group membership(s)", groups_linked)

    logger.info("=" * 60)
    logger.info(
        "Enrichment complete: %d/%d people enriched", enriched, len(person_files)
    )

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
