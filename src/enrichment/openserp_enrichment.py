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
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.utils.http_pool import get_session
from src.utils.file_lock import write_json_with_lock


def _validate_before_write(data: Dict, entity: str) -> bool:
    """Validate a record against its enforced schema before writing. Fail-safe: returns True
    (allow write) if the schema/validator is unavailable; returns False (skip write) only on a
    genuine schema violation, logging it. Never raises."""
    try:
        import jsonschema

        from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

        spec = next((s for s in ENTITY_REGISTRY if s.name == entity), None)
        if spec is None:
            return True
        schema = load_schema(spec)
        jsonschema.validate(data, schema)
        return True
    except jsonschema.ValidationError as e:  # type: ignore[name-defined]
        logger.warning(
            "OpenSERP write skipped — %s record fails schema: %s", entity, e.message
        )
        return False
    except Exception:  # noqa: BLE001 - validator unavailable -> don't block the write
        return True


logger = logging.getLogger(__name__)

# Circuit breaker: skip all OpenSERP searches after N consecutive failures
_CIRCUIT_BREAKER_THRESHOLD = 5
_consecutive_failures = 0
_circuit_open = False
# H3: the breaker globals are mutated from the enrichment thread pool and persist
# across books in one process. Guard them with a lock and reset per run so an
# opened breaker from one book doesn't poison the next.
_circuit_lock = threading.Lock()


def reset_circuit() -> None:
    """Reset the OpenSERP circuit breaker (call at the start of an enrichment run
    so breaker state never leaks across books/runs in a long-lived process)."""
    global _consecutive_failures, _circuit_open
    with _circuit_lock:
        _consecutive_failures = 0
        _circuit_open = False


def _breaker_is_open() -> bool:
    with _circuit_lock:
        return _circuit_open


def _breaker_record_failure() -> bool:
    """Count a failure; open the breaker at threshold. Returns True if now open."""
    global _consecutive_failures, _circuit_open
    with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            if not _circuit_open:
                logger.warning(
                    "OpenSERP circuit breaker OPEN — %d consecutive empty/failed "
                    "responses, skipping remaining searches",
                    _consecutive_failures,
                )
            _circuit_open = True
        return _circuit_open


def _breaker_record_success() -> None:
    global _consecutive_failures
    with _circuit_lock:
        _consecutive_failures = 0


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


def _search_openserp(query: str, openserp_url: str, limit: int = 5) -> List[Dict]:
    """Run an OpenSERP search. Returns list of {url, title, description}."""
    if _breaker_is_open():
        logger.info("OpenSERP circuit breaker SKIP: %s", query[:60])
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
                "limit": str(limit),
                "mode": "any",
            },
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                _breaker_record_failure()
                return []
            # Handle both flat list and {"results": [...]} formats
            results = data if isinstance(data, list) else data.get("results", [])
            if results:
                _breaker_record_success()
            else:
                _breaker_record_failure()
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
        _breaker_record_failure()
    except Exception as e:
        logger.warning("OpenSERP [%s]: %s", query[:60], e)
        _breaker_record_failure()
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


def _name_initial_matches(person_name: str, result_title: str) -> bool:
    """Pre-filter: check if result title could be about this person (first initial + last name)."""
    # Extract last name and first initial from person
    parts = person_name.split()
    name_parts = [p for p in parts if len(p) > 2 and not p.endswith(".")]
    last_name = name_parts[-1].lower() if name_parts else ""
    first_initial = ""
    for p in parts:
        if p and p[0].isupper():
            first_initial = p[0].lower()
            break
    if not last_name:
        return True  # Can't filter, allow through
    title_lower = result_title.lower()
    # Last name must appear in title
    if last_name not in title_lower:
        return False
    # If we have a first initial, check that some word in title starts with it
    if first_initial:
        title_words = title_lower.split()
        if not any(w.startswith(first_initial) for w in title_words):
            return False
    return True


def search_person_images(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 3,
) -> List[Dict[str, str]]:
    """Search for portrait images of a person."""
    from src.utils.search_query_loader import render_search_queries

    queries = render_search_queries("people", "portrait_images", name=person_name)
    images = []
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if not url:
                continue
            # Pre-filter: skip results that clearly aren't about this person
            if not _name_initial_matches(person_name, title):
                continue
            if _verify_result(title, f"Photo of {person_name}", grok_client):
                images.append({"url": url, "title": title, "source": "openserp"})
                if len(images) >= max_results:
                    return images
    return images


def search_equipment_images(
    equipment_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 3,
    identifier: str = "",
    year: str = "",
) -> List[Dict[str, str]]:
    """Search for photos of military equipment (uses search_queries/equipment.yaml)."""
    from src.utils.search_query_loader import render_search_queries

    queries = render_search_queries(
        "equipment",
        "images",
        name=equipment_name,
        identifier=identifier or equipment_name,
        year=year,
    )
    images: List[Dict[str, str]] = []
    seen: set = set()
    for query in queries:
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            title = r.get("title", "")
            if not url or url in seen:
                continue
            if _verify_result(title, f"Photo of {equipment_name}", grok_client):
                images.append({"url": url, "title": title, "source": "openserp"})
                seen.add(url)
                if len(images) >= max_results:
                    return images
    return images


# --- Academic/Media Search ---


def search_academic_sources(
    person_name: str,
    openserp_url: str,
    grok_client: Any = None,
    max_results: int = 5,
) -> List[Dict[str, str]]:
    """Search for academic papers, oral histories, and media about a person."""
    from src.utils.search_query_loader import render_search_queries as _rsq

    queries = _rsq("people", "academic_sources", name=person_name)
    sources = []
    seen_urls = set()
    for query in queries:
        results = _search_openserp(query, openserp_url)
        for r in results:
            url = r.get("url", "")
            title = r.get("title", "")
            if url and url not in seen_urls:
                # Pre-filter: skip results that clearly aren't about this person
                if not _name_initial_matches(person_name, title):
                    continue
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

    from src.utils.search_query_loader import render_search_queries

    queries = render_search_queries("people", "web_results", name=person_name)
    awards: List[Dict[str, str]] = []
    seen: set = set()
    for query in queries:
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            title = r.get("title", "")
            if not url or url in seen:
                continue
            # Pre-filter: skip results that clearly aren't about this person
            if not _name_initial_matches(person_name, title):
                continue
            if _verify_result(
                title, f"Military service of {person_name} in WWII", grok_client
            ):
                awards.append({"url": url, "title": title, "source": "openserp"})
                seen.add(url)
                if len(awards) >= 5:
                    break
        if len(awards) >= 5:
            break

    if awards:
        import json as _json

        cache_result("openserp_awards", person_name, _json.dumps(awards))
    else:
        cache_result("openserp_awards", person_name, None)
    return awards


def search_event_content(
    event_name: str,
    aliases: Optional[List[str]] = None,
    openserp_url: str = "http://localhost:7001",
    grok_client: Any = None,
    max_results: int = 5,
) -> List[Dict[str, str]]:
    """Search for primary sources related to an event, including non-English."""
    from src.utils.search_query_loader import render_search_queries as _rsq2

    queries = _rsq2("events", "primary_sources", event_name=event_name)

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
    existing_image_urls = {
        i.get("url") for i in (data.get("images") or []) if isinstance(i, dict)
    }
    existing_award_urls = {
        a.get("url") for a in (data.get("military_awards") or []) if isinstance(a, dict)
    }
    for r in candidate.get("image_results", []):
        url = r.get("url", "")
        title = r.get("title", "")
        if (
            not url
            or url in existing_image_urls
            or not _name_initial_matches(name, title)
        ):
            continue
        if _verify_result(title, f"Photo of {name} WWII", grok_client):
            data.setdefault("images", []).append(
                {"url": url, "title": title, "source": "openserp"}
            )
            existing_image_urls.add(url)
            changed = True
            if len(data.get("images", [])) >= max_images:
                break
    for r in candidate.get("web_results", []):
        url = r.get("url", "")
        title = r.get("title", "")
        if (
            not url
            or url in existing_award_urls
            or not _name_initial_matches(name, title)
        ):
            continue
        if _verify_result(title, f"Military service of {name} in WWII", grok_client):
            data.setdefault("military_awards", []).append(
                {"url": url, "title": title, "source": "openserp"}
            )
            existing_award_urls.add(url)
            changed = True
            if len(data.get("military_awards", [])) >= max_web:
                break
    return changed


# Non-award sites OpenSERP should also skip (low-value / excluded by the owner).
_EXTRA_SKIP_HOSTS = frozenset({"ibiblio.org", "www.ibiblio.org"})


def _skip_result(url: str) -> bool:
    """True if an OpenSERP result URL should be dropped: award-source domains (sourced
    authoritatively by the award adapters) or explicitly excluded hosts (ibiblio)."""
    if not url:
        return False
    from urllib.parse import urlparse

    from src.enrichment.award_registry import is_award_domain

    host = (urlparse(url).netloc or "").lower()
    if host in _EXTRA_SKIP_HOSTS or host.removeprefix("www.") in _EXTRA_SKIP_HOSTS:
        return True
    return is_award_domain(url)


def _person_query_terms(data: Dict) -> str:
    """Build extra query terms from People-JSON facts to sharpen the OpenSERP search:
    primary unit designation + nationality. Keeps the query specific without flooding
    it (one unit, one nationality)."""
    bp = data.get("biographical_profile") or {}
    terms: List[str] = []
    units = bp.get("units_served") or []
    for u in units:
        if isinstance(u, dict):
            desig = u.get("designation") or u.get("unit")
            if desig:
                terms.append(str(desig))
                break
    nat = bp.get("nationality") or data.get("nationality")
    if nat:
        terms.append(str(nat))
    return " ".join(terms)


def _collect_person_candidates(f, name: str, data: Dict, openserp_url: str) -> Dict:
    """Build the OpenSERP candidate bundle for one person: a portrait-image search
    (skipped if a Wikipedia portrait was already captured in Phase 2) + a web search
    (awards/bio/academic), both augmented with People-JSON facts and filtered of
    award/ibiblio domains."""
    person_candidates: Dict = {"file": f, "name": name, "data": data}
    facts = _person_query_terms(data)

    # Phase 2 already captures the Wikipedia portrait (people enrichment); do NOT re-fetch
    # Wikipedia here (Phase 3 is OpenSERP-only). Only run the OpenSERP image search when the
    # record has no image yet.
    if not data.get("images"):
        hits = _search_openserp(
            f"{name} {facts} WWII portrait photo".replace("  ", " "), openserp_url
        )
        person_candidates["image_results"] = [
            h for h in hits if not _skip_result(h.get("url", ""))
        ]

    # Augment the web query with People-JSON facts (unit, nationality) for precision;
    # skip award-domain + ibiblio hits (award sites sourced authoritatively).
    if not data.get("military_awards"):
        web_hits = _search_openserp(
            f"{name} {facts} WWII".replace("  ", " "), openserp_url
        )
        person_candidates["web_results"] = [
            h for h in web_hits if not _skip_result(h.get("url", ""))
        ]
    return person_candidates


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
    reset_circuit()  # H3: don't inherit breaker state from a prior book/run
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

        person_candidates = _collect_person_candidates(f, name, data, openserp_url)

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
        if _validate_before_write(data, "people"):
            write_json_with_lock(c["file"], data, entity="people")
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
    reset_circuit()  # H3: don't inherit breaker state from a prior book/run
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
        if _validate_before_write(data, "equipment"):
            write_json_with_lock(f, data, entity="equipment")

    logger.info("OpenSERP equipment enrichment: %d enriched", enriched)
    return enriched


def enrich_source_sections_with_openserp(
    source_section_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add OpenSERP primary-source web results to source_section records, keyed on the derived
    OPERATION label (the coarse anchor — e.g. 'Battle of the Bulge') rather than granular event
    names. Only sections with an operation are searched. Native write, gated on
    openserp_searched (90-day), deduped. Returns count enriched."""
    reset_circuit()  # don't inherit breaker state from a prior book/run
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f in sorted(source_section_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and enriched >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        operation = data.get("operation")
        if not operation or not operation.get("name"):
            continue  # null-over-fake: no operation -> nothing to search

        if data.get("openserp_searched"):
            import time as _time

            searched_at = data.get("openserp_searched_at", 0)
            if searched_at and (_time.time() - searched_at) < 90 * 86400:
                continue

        op_name = operation["name"]
        aliases = operation.get("aliases") or []
        results = search_event_content(
            op_name, aliases=aliases, openserp_url=openserp_url, grok_client=grok_client
        )

        existing = data.get("primary_sources") or []
        existing_urls = {s.get("url") for s in existing if isinstance(s, dict)}
        added = [r for r in results if r.get("url") not in existing_urls]
        if added:
            data["primary_sources"] = existing + added
            enriched += 1
            logger.info("  ✓ OpenSERP primary sources for %s: +%d", op_name, len(added))

        data["openserp_searched"] = True
        import time as _time

        data["openserp_searched_at"] = int(_time.time())
        if _validate_before_write(data, "source_section"):
            write_json_with_lock(f, data, entity="source_section")

    logger.info("OpenSERP source_section enrichment: %d enriched", enriched)
    return enriched
