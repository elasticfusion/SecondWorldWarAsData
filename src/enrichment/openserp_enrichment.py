"""OpenSERP-based enrichment for Phase 3 — images, academic sources, event content.

Searches for:
  - People: portraits, academic papers, oral histories, video interviews
  - Equipment: photos, technical drawings
  - Events: primary sources, veteran interviews, documentary footage (multi-language)

All results verified by Grok before acceptance.
Requires OpenSERP service running (ECS Fargate or localhost:7001).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.utils.http_pool import get_session

logger = logging.getLogger(__name__)

SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    ".processed_events.json",
}


def _openserp_reachable(openserp_url: str) -> bool:
    """Check if OpenSERP is reachable with a quick connection test."""
    try:
        session = get_session()
        resp = session.get(f"{openserp_url}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _search_openserp(
    query: str, openserp_url: str, engines: Optional[List[str]] = None
) -> List[Dict]:
    """Run an OpenSERP search. Returns list of {url, title, description}."""
    try:
        session = get_session()
        resp = session.post(
            f"{openserp_url}/search",
            json={"query": query, "engines": engines or ["google"]},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("results", [])
    except Exception as e:
        logger.debug("OpenSERP search failed: %s", e)
    return []


def _verify_result(
    candidate_title: str, expected_context: str, grok_client: Any
) -> bool:
    """Use Grok to verify a search result is relevant."""
    if not grok_client:
        return True
    try:
        response = grok_client.chat_completion(
            prompt=f'Is this search result relevant?\nContext: "{expected_context[:200]}"\nResult: "{candidate_title[:200]}"\nReturn ONLY "YES" or "NO".',
            system_prompt="You verify search result relevance.",
            temperature=0.0,
            use_cache=True,
            cache_type="openserp_verify",
        )
        return response.strip().upper().startswith("YES")
    except Exception:
        return True


# --- Image Search ---


def search_person_images(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 3,
) -> List[Dict[str, str]]:
    """Search for portrait images of a person."""
    queries = [
        f'"{person_name}" WWII portrait photo',
        f'"{person_name}" World War II general officer photo',
    ]
    images = []
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if url and _verify_result(title, f"Photo of {person_name}", grok_client):
                images.append({"url": url, "title": title, "source": "openserp"})
                if len(images) >= max_results:
                    return images
    return images


def search_equipment_images(
    equipment_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 3,
) -> List[Dict[str, str]]:
    """Search for photos of military equipment."""
    results = _search_openserp(f'"{equipment_name}" WWII photo', openserp_url)
    images = []
    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        if url and _verify_result(title, f"Photo of {equipment_name}", grok_client):
            images.append({"url": url, "title": title, "source": "openserp"})
            if len(images) >= max_results:
                break
    return images


# --- Academic/Media Search ---


def search_academic_sources(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 5,
) -> List[Dict[str, str]]:
    """Search for academic papers, oral histories, and media about a person."""
    queries = [
        f'"{person_name}" oral history WWII',
        f'"{person_name}" university archive World War II',
        f'"{person_name}" documentary interview WWII',
    ]
    sources = []
    seen_urls = set()
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if url and url not in seen_urls:
                if _verify_result(
                    title, f"Academic/media about {person_name}", grok_client
                ):
                    sources.append(
                        {
                            "url": url,
                            "title": title,
                            "type": _classify_source(url, title),
                            "source": "openserp",
                        }
                    )
                    seen_urls.add(url)
                    if len(sources) >= max_results:
                        return sources
    return sources


def _classify_source(url: str, title: str) -> str:
    """Classify a source by URL/title patterns."""
    url_lower = url.lower()
    title_lower = title.lower()
    if "oral history" in title_lower or "interview" in title_lower:
        return "oral_history"
    if (
        "youtube.com" in url_lower
        or "video" in title_lower
        or "documentary" in title_lower
    ):
        return "video"
    if ".edu" in url_lower or "university" in title_lower or "journal" in title_lower:
        return "academic"
    if "archive" in url_lower or "museum" in url_lower:
        return "archive"
    return "other"


# --- Event Content Search ---


def search_event_content(
    event_name: str,
    aliases: Optional[List[str]] = None,
    openserp_url: str = "http://localhost:7001",
    grok_client: Any = None,
    max_results: int = 5,
) -> List[Dict[str, str]]:
    """Search for primary sources related to an event, including non-English."""
    queries = [f'"{event_name}" veteran interview primary source']

    # Add non-English queries for major events
    if aliases:
        for alias in aliases[:2]:
            queries.append(f'"{alias}" témoignage')  # French: testimony
            queries.append(f'"{alias}" Zeitzeuge')  # German: eyewitness

    sources = []
    seen_urls = set()
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if url and url not in seen_urls:
                if _verify_result(title, f"Content about {event_name}", grok_client):
                    sources.append(
                        {
                            "url": url,
                            "title": title,
                            "type": _classify_source(url, title),
                            "source": "openserp",
                        }
                    )
                    seen_urls.add(url)
                    if len(sources) >= max_results:
                        return sources
    return sources


# --- Batch Enrichment ---


def enrich_people_with_openserp(
    people_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images and academic sources to people files. Returns count enriched."""
    # Verify OpenSERP is reachable before processing
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f in sorted(people_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and enriched >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        if data.get("openserp_searched"):
            continue

        name = data.get("name", "")
        if not name:
            continue

        changed = False

        # Images
        if not data.get("images"):
            images = search_person_images(name, openserp_url, grok_client)
            if images:
                data["images"] = images
                changed = True

        # Academic/media sources
        if not data.get("academic_references"):
            refs = search_academic_sources(name, openserp_url, grok_client)
            if refs:
                data["academic_references"] = refs
                changed = True

        data["openserp_searched"] = True
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if changed:
            enriched += 1
            logger.info("  ✓ OpenSERP enriched: %s", name)

    logger.info("OpenSERP people enrichment: %d enriched", enriched)
    return enriched


def enrich_equipment_with_openserp(
    equipment_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images to equipment files. Returns count enriched."""
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f in sorted(equipment_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and enriched >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        if data.get("openserp_searched"):
            continue

        name = data.get("common_name", data.get("name", ""))
        if not name:
            continue

        if not data.get("images"):
            images = search_equipment_images(name, openserp_url, grok_client)
            if images:
                data["images"] = images
                enriched += 1
                logger.info("  ✓ OpenSERP enriched: %s", name)

        data["openserp_searched"] = True
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("OpenSERP equipment enrichment: %d enriched", enriched)
    return enriched
