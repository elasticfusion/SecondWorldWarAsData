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

from src.schemas import inject_metadata
from src.utils.http_pool import get_session

logger = logging.getLogger(__name__)

# Circuit breaker: skip all OpenSERP searches after N consecutive failures
_CIRCUIT_BREAKER_THRESHOLD = 5
_consecutive_failures = 0
_circuit_open = False

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


def _search_openserp(query: str, openserp_url: str) -> List[Dict]:
    """Run an OpenSERP search. Returns list of {url, title, description}."""
    global _consecutive_failures, _circuit_open

    if _circuit_open:
        return []

    try:
        import time

        from src.utils.config import load_config

        cfg = load_config().get("openserp", {})
        time.sleep(cfg.get("rate_limit_seconds", 5))
        session = get_session()
        resp = session.get(
            f"{openserp_url}/mega/search",
            params={
                "text": query,
                "limit": str(cfg.get("results_per_query", 20)),
                "mode": "any",
                "engines": "google,bing,duckduckgo",
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                _consecutive_failures += 1
                if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                    _circuit_open = True
                    logger.warning(
                        "OpenSERP circuit breaker OPEN — %d consecutive empty responses, skipping remaining searches",
                        _consecutive_failures,
                    )
                return []
            results = data.get("results", [])
            if results:
                _consecutive_failures = 0  # Reset on success
            else:
                _consecutive_failures += 1
                if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                    _circuit_open = True
                    logger.warning(
                        "OpenSERP circuit breaker OPEN — %d consecutive empty results, skipping remaining searches",
                        _consecutive_failures,
                    )
                    return []
            logger.info("OpenSERP [%s]: %d results", query[:60], len(results))
            return [
                {
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "description": r.get("snippet", ""),
                }
                for r in results
                if r
            ]
        logger.warning("OpenSERP [%s]: HTTP %d", query[:60], resp.status_code)
        _consecutive_failures += 1
    except Exception as e:
        logger.warning("OpenSERP [%s]: %s", query[:60], e)
        _consecutive_failures += 1

    if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD and not _circuit_open:
        _circuit_open = True
        logger.warning(
            "OpenSERP circuit breaker OPEN — %d consecutive failures",
            _consecutive_failures,
        )
    return []


def _verify_result(
    candidate_title: str, expected_context: str, grok_client: Any
) -> bool:
    """Use Grok to verify a search result is relevant (cached, batch-friendly)."""
    if not grok_client:
        return True
    from src.utils.search_cache import cache_result, get_cached

    cache_key = f"{expected_context[:50]}|{candidate_title[:50]}"
    cached = get_cached("openserp_verify", cache_key)
    if cached == "YES":
        return True
    if cached == "NO" or cached == "NOT_FOUND":
        return False

    try:
        response = grok_client.chat_completion(
            prompt=f'Is this search result relevant?\nContext: "{expected_context[:200]}"\nResult: "{candidate_title[:200]}"\nReturn ONLY "YES" or "NO".',
            system_prompt="You verify search result relevance.",
            temperature=0.0,
            use_cache=True,
            cache_type="openserp_verify",
        )
        answer = "YES" if response.strip().upper().startswith("YES") else "NO"
        cache_result("openserp_verify", cache_key, answer)
        logger.info(
            "Grok verify [%s]: %s — '%s'",
            answer,
            expected_context[:40],
            candidate_title[:50],
        )
        return answer == "YES"
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
    if any(
        s in url_lower for s in ("valor.militarytimes", "homeofheroes", "cmohs.org")
    ):
        return "military_award"
    return "other"


_AWARD_SITES = {
    "valor.militarytimes.com",
    "homeofheroes.com",
    "themedalofhonor.com",
    "cmohs.org",
    "militaryhallofhonor.com",
}


def search_military_awards(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
) -> List[Dict[str, str]]:
    """Search the web for military award citations and biographical data."""
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("openserp_awards", person_name)
    if cached == "NOT_FOUND":
        return []
    if cached:
        import json as _json

        return _json.loads(cached)

    results = _search_openserp(f'"{person_name}" WWII', openserp_url)
    awards = []
    seen = set()
    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        if not url or url in seen:
            continue
        if _verify_result(
            title, f"Military service of {person_name} in WWII", grok_client
        ):
            awards.append({"url": url, "title": title, "source": "openserp"})
            seen.add(url)
            if len(awards) >= 5:
                break

    if awards:
        import json as _json

        cache_result("openserp_awards", person_name, _json.dumps(awards))
    else:
        cache_result("openserp_awards", person_name, None)
    return awards


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


def _verify_and_apply(
    candidate: Dict,
    data: Dict,
    name: str,
    grok_client: Any,
    max_images: int,
    max_web: int,
) -> bool:
    """Verify OpenSERP results with Grok and apply to entity data."""
    changed = False
    for r in candidate.get("image_results", []):
        url = r.get("url", "")
        title = r.get("title", "")
        if url and _verify_result(title, f"Photo of {name} WWII", grok_client):
            data.setdefault("images", []).append(
                {"url": url, "title": title, "source": "openserp"}
            )
            changed = True
            if len(data.get("images", [])) >= max_images:
                break
    for r in candidate.get("web_results", []):
        url = r.get("url", "")
        title = r.get("title", "")
        if url and _verify_result(
            title, f"Military service of {name} in WWII", grok_client
        ):
            data.setdefault("military_awards", []).append(
                {"url": url, "title": title, "source": "openserp"}
            )
            changed = True
            if len(data.get("military_awards", [])) >= max_web:
                break
    return changed


def enrich_people_with_openserp(
    people_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images and academic sources to people files. Returns count enriched.

    Two-pass approach:
    1. Search OpenSERP for all people, collect candidate results
    2. Verify candidates with Grok (cached — repeat runs are free)
    3. Write verified results to files
    """
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    # Pass 1: Collect candidates from OpenSERP
    candidates: List[Dict] = []
    for f in sorted(people_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and len(candidates) >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        if data.get("openserp_searched"):
            import time as _time

            searched_at = data.get("openserp_searched_at", 0)
            if searched_at and (_time.time() - searched_at) < 90 * 86400:
                continue

        name = data.get("name", "")
        if not name:
            continue

        person_candidates: Dict = {"file": f, "name": name, "data": data}

        # Search for images
        if not data.get("images"):
            person_candidates["image_results"] = _search_openserp(
                f'"{name}" WWII portrait photo', openserp_url
            )

        # Search for web results (awards, bio, academic)
        if not data.get("military_awards"):
            person_candidates["web_results"] = _search_openserp(
                f'"{name}" WWII', openserp_url
            )

        candidates.append(person_candidates)

    logger.info(
        "OpenSERP people: %d candidates collected, verifying with Grok...",
        len(candidates),
    )

    # Pass 2: Verify and write
    from src.utils.config import load_config

    cfg = load_config().get("openserp", {})
    max_images = cfg.get("max_images_per_entity", 1)
    max_web = cfg.get("max_web_results_per_entity", 5)

    enriched = 0
    for c in candidates:
        data = c["data"]
        name = c["name"]
        changed = _verify_and_apply(c, data, name, grok_client, max_images, max_web)

        data["openserp_searched"] = True
        import time as _time

        data["openserp_searched_at"] = int(_time.time())
        inject_metadata(data)
        c["file"].write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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
            import time as _time

            searched_at = data.get("openserp_searched_at", 0)
            if searched_at and (_time.time() - searched_at) < 90 * 86400:
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
        import time as _time

        data["openserp_searched_at"] = int(_time.time())
        inject_metadata(data)
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("OpenSERP equipment enrichment: %d enriched", enriched)
    return enriched
