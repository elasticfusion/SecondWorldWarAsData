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
import datetime as _dt
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
                _metric("breaker_opened")
            _circuit_open = True
        return _circuit_open


def _breaker_record_success() -> None:
    global _consecutive_failures
    with _circuit_lock:
        _consecutive_failures = 0


logger = logging.getLogger(__name__)


# --- OpenSERP effectiveness/health metrics (per Phase-3 run) ---------------------------------
_metrics_lock = threading.Lock()
_METRIC_KEYS = (
    "entities_searched",
    "entities_enriched",
    "queries_issued",
    "results_returned",
    "zero_result_queries",
    "verify_yes",
    "verify_no",
    "breaker_opened",
    "urls_fetched",
    "items_added",
)
_metrics: Dict[str, int] = {k: 0 for k in _METRIC_KEYS}


def _metric(key: str, n: int = 1) -> None:
    """Increment an OpenSERP metric (thread-safe)."""
    with _metrics_lock:
        _metrics[key] = _metrics.get(key, 0) + n


def reset_metrics() -> None:
    with _metrics_lock:
        for k in _METRIC_KEYS:
            _metrics[k] = 0


def get_metrics() -> Dict[str, Any]:
    """Snapshot of the metrics + derived rates (hit rate, verify pass rate)."""
    with _metrics_lock:
        m: Dict[str, Any] = dict(_metrics)
    searched = m["entities_searched"] or 1
    verified = (m["verify_yes"] + m["verify_no"]) or 1
    queries = m["queries_issued"] or 1
    m["enrichment_rate"] = round(m["entities_enriched"] / searched, 3)
    m["verify_pass_rate"] = round(m["verify_yes"] / verified, 3)
    m["zero_result_rate"] = round(m["zero_result_queries"] / queries, 3)
    return m


def write_metrics(output_dir: Any) -> Dict[str, Any]:
    """Write the OpenSERP metrics snapshot to output/metrics/openserp_metrics.json. Returns the
    snapshot (also suitable for embedding in .phase_results.json). Fail-safe."""
    import json as _json
    from datetime import datetime, timezone

    snap = get_metrics()
    snap["generated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        mdir = output_dir / "metrics"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "openserp_metrics.json").write_text(
            _json.dumps(snap, indent=2), encoding="utf-8"
        )
        logger.info(
            "OpenSERP metrics: searched=%d enriched=%d (%.0f%% hit) | queries=%d results=%d "
            "zero=%d | verify %d/%d (%.0f%% pass) | breaker_open=%d | items_added=%d",
            snap["entities_searched"],
            snap["entities_enriched"],
            snap["enrichment_rate"] * 100,
            snap["queries_issued"],
            snap["results_returned"],
            snap["zero_result_queries"],
            snap["verify_yes"],
            snap["verify_yes"] + snap["verify_no"],
            snap["verify_pass_rate"] * 100,
            snap["breaker_opened"],
            snap["items_added"],
        )
    except Exception as e:  # noqa: BLE001 - metrics are best-effort
        logger.debug("Could not write OpenSERP metrics: %s", e)
    return snap


def _render_queries(query_file: str, category: str, **kwargs) -> List[str]:
    """Render search-query templates and squeeze whitespace, so an empty {facts} (or any empty
    variable) collapses cleanly instead of leaving double spaces / a dangling literal.
    """
    import re as _re

    from src.utils.search_query_loader import render_search_queries

    out = []
    for q in render_search_queries(query_file, category, **kwargs):
        q = _re.sub(r"\{[a-z_]+\}", "", q)  # drop any unfilled placeholder
        out.append(_re.sub(r"\s+", " ", q).strip())
    return out


SKIP_FILES = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    "not_related.json",
    ".processed_events.json",
}


def _openserp_reachable(openserp_url: str) -> bool:
    """Check if OpenSERP is reachable with a FAST, single-shot probe.

    Uses a plain requests.get (NOT the shared session, which carries a urllib3 Retry adapter)
    so an unreachable OpenSERP fails in one short attempt instead of 3 retries + backoff per
    call — important because this is probed once per entity step (local/dev runs with OpenSERP
    down otherwise spent ~30s retrying)."""
    try:
        resp = requests.get(f"{openserp_url}/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _search_openserp(query: str, openserp_url: str, limit: int = 5) -> List[Dict]:
    """Run an OpenSERP search. Returns list of {url, title, description}."""
    if _breaker_is_open():
        logger.info("OpenSERP circuit breaker SKIP: %s", query[:60])
        return []

    _metric("queries_issued")
    try:
        import time

        from src.utils.config import load_config

        cfg = load_config().get("openserp", {})
        time.sleep(cfg.get("rate_limit_seconds", 5))
        session = get_session()
        # Browser-mode OpenSERP (headless Chromium across several engines) can be slow;
        # the per-request timeout is config-driven so it can absorb browser latency
        # without tripping the breaker on a slow-but-valid query.
        req_timeout = cfg.get("request_timeout_seconds", 60)
        resp = session.get(
            f"{openserp_url}/mega/search",
            params={
                "text": query,
                "limit": str(limit),
                "mode": "any",
            },
            timeout=req_timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                _breaker_record_failure()
                _metric("zero_result_queries")
                return []
            # Handle both flat list and {"results": [...]} formats
            results = data if isinstance(data, list) else data.get("results", [])
            if results:
                _breaker_record_success()
            else:
                _breaker_record_failure()
                _metric("zero_result_queries")
                return []
            logger.info("OpenSERP [%s]: %d results", query[:60], len(results))
            _metric("results_returned", len(results))
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
    candidate_title: str,
    expected_context: str,
    grok_client: Any,
    snippet: str = "",
    url: str = "",
) -> bool:
    """Use Grok to verify a search result is relevant (cached, batch-friendly).

    FAIL-CLOSED: with no grok_client, or on any Grok error, return False (reject) rather than
    accepting an unverified result. The judgment uses the title, the result snippet, AND the
    URL (not the title alone) for a stronger signal."""
    if not grok_client:
        # No verifier available -> cannot confirm relevance -> reject (fail-closed).
        return False
    from src.utils.search_cache import cache_result, get_cached

    cache_key = f"{expected_context[:50]}|{candidate_title[:50]}|{url[:60]}"
    cached = get_cached("openserp_verify", cache_key)
    if cached == "YES":
        return True
    if cached == "NO" or cached == "NOT_FOUND":
        return False

    try:
        response = grok_client.chat_completion(
            prompt=(
                "Is this search result relevant to the context?\n"
                f'Context: "{expected_context[:200]}"\n'
                f'Result title: "{candidate_title[:200]}"\n'
                f'Result snippet: "{snippet[:300]}"\n'
                f"Result URL: {url[:200]}\n"
                'Return ONLY "YES" or "NO".'
            ),
            system_prompt="You verify search result relevance.",
            temperature=0.0,
            use_cache=True,
            cache_type="openserp_verify",
        )
        answer = "YES" if response.strip().upper().startswith("YES") else "NO"
        cache_result("openserp_verify", cache_key, answer)
        _metric("verify_yes" if answer == "YES" else "verify_no")
        logger.info(
            "Grok verify [%s]: %s — '%s'",
            answer,
            expected_context[:40],
            candidate_title[:50],
        )
        return answer == "YES"
    except Exception:
        # Verifier errored -> cannot confirm -> reject (fail-closed). NOT cached, so a later
        # run with a healthy Grok can still verify it.
        logger.warning(
            "Grok verify errored (fail-closed reject): %s", candidate_title[:50]
        )
        return False


# --- Positive-URL page processing (fetch -> summarize -> 90-day retention) ---------------

_URL_FETCH_RETENTION_DAYS = (
    90  # matches DEFAULT_RECHECK_SECONDS / openserp_searched gate
)


def _summarize_url_page(
    url: str,
    context: str,
    grok_client: Any,
    provenance: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Fetch a discovered page and return a short Grok summary of its content, or None.

    90-day retention: a URL already processed within the window returns its cached summary and
    is NOT re-fetched (handles both a prior hit and a prior empty result). Requires a
    grok_client (fail-closed: no client -> no summary). Fail-safe: fetch/summarize errors
    return None without raising.

    If ``provenance`` is supplied and the content had to be recovered from the Internet
    Archive Wayback Machine (live page WAF-blocked/dead), it is populated with
    ``archived_url`` + ``wayback_capture_timestamp`` so the caller can record the
    archive retrieval alongside the original URL in the bibliographical record."""
    if not grok_client or not url:
        return None
    from src.utils.search_cache import _get_backend, _make_key

    backend = _get_backend()
    key = _make_key("openserp_url_summary", url)
    cached = backend.get(key)
    if cached is not None:
        # Already processed within the retention window (summary or sentinel "") -> skip fetch.
        return cached or None

    summary: Optional[str] = None
    try:
        from src.extraction.enrich_biographies import _fetch_url_content

        prov: Dict[str, Any] = {}
        html = _fetch_url_content(url, provenance=prov)
        if html:
            import re as _re

            text = _re.sub(r"<[^>]+>", " ", html)
            text = _re.sub(r"\s+", " ", text).strip()[:6000]
            if text:
                resp = grok_client.chat_completion(
                    prompt=(
                        f"Summarize this web page in 1-2 sentences, focused on its relevance "
                        f'to: "{context[:150]}".\n\nURL: {url}\n\nContent:\n{text}'
                    ),
                    system_prompt="You summarize web pages concisely.",
                    temperature=0.1,
                    use_cache=True,
                    cache_type="openserp_url_summary",
                )
                summary = (resp or "").strip() or None
                # If recovered via the Wayback Machine, surface the archive provenance
                # (structured) so the caller records original URL + archive retrieval.
                if (
                    summary
                    and prov.get("source") == "wayback"
                    and provenance is not None
                ):
                    provenance["archived_url"] = prov.get("archived_url", "")
                    provenance["wayback_capture_timestamp"] = prov.get(
                        "wayback_capture_timestamp", ""
                    )
    except Exception as e:  # noqa: BLE001 - best-effort; never block enrichment
        logger.debug("URL summarize failed for %s: %s", url, e)

    # Record the URL as processed (store "" sentinel on a miss) with 90-day retention so it is
    # not re-fetched for the retention period.
    try:
        backend.put(key, summary or "", ttl_days=_URL_FETCH_RETENTION_DAYS)
    except Exception:  # noqa: BLE001
        pass
    return summary


def _url_verdict_cached(url: str) -> Optional[str]:
    """Return a cached URL verdict: 'REJECT' (negative — skip), a summary string (positive), or
    None (never processed). Negative + positive URLs are both cached (90-day) to prevent
    reprocessing on future runs."""
    if not url:
        return None
    try:
        from src.utils.search_cache import _get_backend, _make_key

        return _get_backend().get(_make_key("openserp_url_verdict", url))
    except Exception:  # noqa: BLE001
        return None


def _cache_url_verdict(url: str, verdict: str) -> None:
    """Cache a URL verdict ('REJECT' or a summary) for the retention window."""
    if not url:
        return
    try:
        from src.utils.search_cache import _get_backend, _make_key

        _get_backend().put(
            _make_key("openserp_url_verdict", url),
            verdict,
            ttl_days=_URL_FETCH_RETENTION_DAYS,
        )
    except Exception:  # noqa: BLE001
        pass


def _wayback_ts_to_iso(ts: str) -> str:
    """Convert a Wayback 14-digit capture timestamp (YYYYMMDDhhmmss) to an ISO-8601
    UTC datetime for the bibliographical record. Returns "" if unparseable."""
    if not ts or len(ts) < 8:
        return ""
    try:
        return (
            _dt.datetime.strptime(ts[:14].ljust(14, "0"), "%Y%m%d%H%M%S")
            .replace(tzinfo=_dt.timezone.utc)
            .isoformat()
        )
    except ValueError:
        return ""


def process_positive_url(
    url: str,
    title: str,
    snippet: str,
    context: str,
    grok_client: Any,
) -> Optional[Dict[str, Any]]:
    """Verify + process one discovered URL into an enriched result, with positive AND negative
    URL caching (both cached 90 days to prevent reprocessing):

      * previously REJECTED or dead URL -> return None immediately (no re-verify/re-fetch);
      * previously processed positive    -> rebuild the result from the cached summary;
      * new URL -> Grok-verify (fail-closed); reject -> cache 'REJECT' + None; accept -> fetch
        + summarize the page, cache the summary, return {url,title,summary,fetched_at,source}.
    """
    import time as _time

    if not url:
        return None
    cached = _url_verdict_cached(url)
    if cached == "REJECT":
        return None
    if cached:  # positive summary cached
        return {
            "url": url,
            "title": title,
            "summary": cached,
            "fetched_at": int(_time.time()),
            "source": "openserp",
        }

    if not _verify_result(title, context, grok_client, snippet=snippet, url=url):
        _cache_url_verdict(url, "REJECT")
        return None

    prov: Dict[str, Any] = {}
    summary = _summarize_url_page(url, context, grok_client, provenance=prov)
    if not summary:
        # Verified-relevant but the page could not be fetched/summarized: still a negative for
        # reprocessing purposes -> cache REJECT so we don't retry the fetch for 90 days.
        _cache_url_verdict(url, "REJECT")
        return None
    _metric("urls_fetched")  # a real page fetch+summarize (cache hits return earlier)
    _cache_url_verdict(url, summary)
    result: Dict[str, Any] = {
        "url": url,  # ALWAYS the original source URL (citation anchor)
        "title": title,
        "summary": summary,
        "fetched_at": int(_time.time()),
        "source": "openserp",
    }
    # If the live page was WAF-blocked/dead and content came from the Internet Archive,
    # record BOTH the original URL (above) and the archive retrieval for the citation:
    # the archived URL, the archive's capture date, and when WE retrieved it.
    if prov.get("archived_url"):
        result["retrieved_from"] = "wayback"
        result["archived_url"] = prov["archived_url"]
        result["wayback_capture_timestamp"] = prov.get("wayback_capture_timestamp", "")
        result["archive_capture_date"] = _wayback_ts_to_iso(
            prov.get("wayback_capture_timestamp", "")
        )
        result["retrieved_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return result


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
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search for portrait images of a person (query sharpened with the person's own facts)."""
    queries = _render_queries(
        "people", "portrait_images", name=person_name, facts=facts
    )
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
            if _verify_result(
                title,
                f"Photo of {person_name}",
                grok_client,
                snippet=r.get("description", ""),
                url=url,
            ):
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
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search for photos of military equipment (uses search_queries/equipment.yaml,
    sharpened with the equipment's own facts: category + country_of_origin)."""
    queries = _render_queries(
        "equipment",
        "images",
        name=equipment_name,
        identifier=identifier or equipment_name,
        year=year,
        facts=facts,
    )
    images: List[Dict[str, str]] = []
    seen: set = set()
    for query in queries:
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            title = r.get("title", "")
            if not url or url in seen:
                continue
            if _verify_result(
                title,
                f"Photo of {equipment_name}",
                grok_client,
                snippet=r.get("description", ""),
                url=url,
            ):
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
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search for academic papers, oral histories, and media about a person."""
    queries = _render_queries(
        "people", "academic_sources", name=person_name, facts=facts
    )
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
    facts: str = "",
) -> List[Dict[str, str]]:
    """Search the web for military award citations and biographical data."""
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("openserp_awards", person_name)
    if cached == "NOT_FOUND":
        return []
    if cached:
        import json as _json

        return _json.loads(cached)

    queries = _render_queries("people", "web_results", name=person_name, facts=facts)
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
                processed = process_positive_url(
                    url,
                    title,
                    r.get("description", ""),
                    f"Content about {event_name}",
                    grok_client,
                )
                if processed:
                    processed["type"] = _classify_source(url, title)
                    sources.append(processed)
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
            _metric("items_added")
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
        processed = process_positive_url(
            url,
            title,
            r.get("description", ""),
            f"Military service of {name} in WWII",
            grok_client,
        )
        if processed:
            data.setdefault("military_awards", []).append(processed)
            existing_award_urls.add(url)
            _metric("items_added")
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
    primary unit designation + rank + nationality. Keeps the query specific without flooding
    it (one unit, one rank, one nationality)."""
    bp = data.get("biographical_profile") or {}
    terms: List[str] = []
    units = bp.get("units_served") or []
    for u in units:
        if isinstance(u, dict):
            desig = u.get("designation") or u.get("unit")
            if desig:
                terms.append(str(desig))
                break
    rank = bp.get("rank") or data.get("rank")
    if rank:
        terms.append(str(rank))
    nat = bp.get("nationality") or data.get("nationality")
    if nat:
        terms.append(str(nat))
    return " ".join(terms)


def _equipment_query_terms(data: Dict) -> str:
    """Build extra query terms from Equipment-JSON facts to sharpen the OpenSERP search:
    category + country_of_origin (the technical_identifier is already a template variable).
    Keeps it specific without flooding the query."""
    terms: List[str] = []
    cat = data.get("category")
    if cat:
        terms.append(str(cat))
    origin = data.get("country_of_origin")
    if origin:
        terms.append(str(origin))
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
    # record has no image yet. Queries come from people.yaml (facts-sharpened), unifying this
    # path with search_person_images rather than a hardcoded query string.
    if not data.get("images"):
        hits: List[Dict[str, str]] = []
        for q in _render_queries("people", "portrait_images", name=name, facts=facts):
            hits.extend(_search_openserp(q, openserp_url))
        person_candidates["image_results"] = [
            h for h in hits if not _skip_result(h.get("url", ""))
        ]

    # Web query from people.yaml web_results (facts-sharpened); skip award-domain + ibiblio
    # hits (award sites are sourced authoritatively elsewhere).
    if not data.get("military_awards"):
        web_hits: List[Dict[str, str]] = []
        for q in _render_queries("people", "web_results", name=name, facts=facts):
            web_hits.extend(_search_openserp(q, openserp_url))
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
        _metric("entities_searched")
        import time as _time

        data["openserp_searched_at"] = int(_time.time())
        if _validate_before_write(data, "people"):
            write_json_with_lock(c["file"], data, entity="people")
            if changed:
                enriched += 1
                _metric("entities_enriched")
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
            images = search_equipment_images(
                name,
                openserp_url,
                grok_client,
                identifier=data.get("technical_identifier") or "",
                facts=_equipment_query_terms(data),
            )
            if images:
                data["images"] = images
                enriched += 1
                _metric("entities_enriched")
                _metric("items_added", len(images))
                logger.info("  ✓ OpenSERP enriched: %s", name)

        data["openserp_searched"] = True
        _metric("entities_searched")
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
            _metric("entities_enriched")
            _metric("items_added", len(added))
            logger.info("  ✓ OpenSERP primary sources for %s: +%d", op_name, len(added))

        data["openserp_searched"] = True
        _metric("entities_searched")
        import time as _time

        data["openserp_searched_at"] = int(_time.time())
        if _validate_before_write(data, "source_section"):
            write_json_with_lock(f, data, entity="source_section")

    logger.info("OpenSERP source_section enrichment: %d enriched", enriched)
    return enriched


def _group_name_matches(group_name: str, result_title: str) -> bool:
    """Pre-filter for unit results: the (often numeric) unit designation must appear in the
    title. Units are highly ambiguous, so this is a coarse guard before Grok verification.
    """
    if not group_name:
        return True
    name_low = group_name.lower()
    title_low = result_title.lower()
    # Require the longest significant token (usually the ordinal/arm, e.g. "panzer",
    # "airborne", or the number) to be present.
    tokens = [t for t in name_low.replace("-", " ").split() if len(t) > 2]
    if not tokens:
        return name_low in title_low
    return any(t in title_low for t in tokens)


def _group_openserp_category(
    group_name: str,
    nationality: str,
    category: str,
    openserp_url: str,
    grok_client: Any,
    max_results: int,
) -> List[Dict[str, Any]]:
    """Run one people_groups.yaml query category (images|web_results|veterans_association)."""
    from src.utils.search_query_loader import render_search_queries

    queries = render_search_queries(
        "people_groups", category, name=group_name, nationality=nationality
    )
    out: List[Dict[str, str]] = []
    seen: set = set()
    context = f"{group_name} ({nationality}) in WWII"
    # Images have no article to summarize -> verify-only. Textual categories
    # (web_results, veterans_association) are fetched + summarized with pos/neg URL caching.
    textual = category != "images"
    for query in queries:
        for r in _search_openserp(query, openserp_url):
            url = r.get("url", "")
            title = r.get("title", "")
            if not url or url in seen or not _group_name_matches(group_name, title):
                continue
            if textual:
                processed = process_positive_url(
                    url, title, r.get("description", ""), context, grok_client
                )
                if processed:
                    out.append(processed)
                    seen.add(url)
            elif _verify_result(
                title, context, grok_client, snippet=r.get("description", ""), url=url
            ):
                out.append({"url": url, "title": title, "source": "openserp"})
                seen.add(url)
            if len(out) >= max_results:
                return out
    return out


def enrich_groups_with_openserp(
    groups_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images, unit-history web results, and veterans-association sites to people_groups
    records. Queries are nationality-disambiguated and WWII-scoped (people_groups.yaml), so a
    same-numbered unit from another conflict/army is not matched. Native write, gated on
    openserp_searched (90-day), deduped. Returns count enriched."""
    reset_circuit()
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    enriched = 0
    for f in sorted(groups_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and enriched >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        name = data.get("group_name", "")
        if not name or len(name) < 3:
            continue

        if data.get("openserp_searched"):
            import time as _time

            searched_at = data.get("openserp_searched_at", 0)
            if searched_at and (_time.time() - searched_at) < 90 * 86400:
                continue

        nationality = data.get("nationality") or ""
        changed = False
        for category, field in (
            ("images", "images"),
            ("web_results", "web_results"),
            ("veterans_association", "veterans_associations"),
        ):
            results = _group_openserp_category(
                name, nationality, category, openserp_url, grok_client, max_results=3
            )
            if not results:
                continue
            existing = data.get(field) or []
            existing_urls = {r.get("url") for r in existing if isinstance(r, dict)}
            added = [r for r in results if r.get("url") not in existing_urls]
            if added:
                data[field] = existing + added
                _metric("items_added", len(added))
                changed = True

        data["openserp_searched"] = True
        _metric("entities_searched")
        import time as _time

        data["openserp_searched_at"] = int(_time.time())
        if _validate_before_write(data, "people_groups"):
            write_json_with_lock(f, data, entity="people_groups")
        if changed:
            enriched += 1
            _metric("entities_enriched")
            logger.info("  ✓ OpenSERP enriched group: %s", name)

    logger.info("OpenSERP people_groups enrichment: %d enriched", enriched)
    return enriched


def place_name_variants(place: Dict) -> List[str]:
    """Return the deduped set of names to search for a place: current_name + name + aliases +
    historical_names[].name (the native-language WWII names Phase 2 already extracted, e.g.
    German 'Pfalz' for Palatinate). Order-preserving, case-insensitive dedup."""
    variants: List[str] = []
    seen: set = set()

    def _add(n: Any) -> None:
        if isinstance(n, str) and n.strip() and n.strip().lower() not in seen:
            seen.add(n.strip().lower())
            variants.append(n.strip())

    _add(place.get("current_name"))
    _add(place.get("name"))
    for a in place.get("aliases") or []:
        _add(a)
    for h in place.get("historical_names") or []:
        _add(h.get("name") if isinstance(h, dict) else h)
    return variants


def enrich_places_with_openserp(
    places_dir: Path,
    openserp_url: str,
    grok_client: Any = None,
    max_items: Optional[int] = None,
) -> int:
    """Add images + source web results to place records, searching EACH name variant
    (current + aliases + native-language historical names), so e.g. a place's German WWII name
    is searched alongside its English name. Textual sources are fetched + summarized via
    process_positive_url (with pos/neg URL caching); images are verify-only. Native write,
    gated on openserp_searched (90-day), deduped. Returns count enriched."""
    reset_circuit()
    if not _openserp_reachable(openserp_url):
        logger.warning("OpenSERP not reachable at %s — skipping", openserp_url)
        return 0

    from src.utils.search_query_loader import render_search_queries

    enriched = 0
    for f in sorted(places_dir.glob("*.json")):
        if f.name in SKIP_FILES:
            continue
        if max_items and enriched >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        variants = place_name_variants(data)
        if not variants:
            continue

        if data.get("openserp_searched"):
            import time as _time

            searched_at = data.get("openserp_searched_at", 0)
            if searched_at and (_time.time() - searched_at) < 90 * 86400:
                continue

        primary = variants[0]
        changed = False

        # Images (verify-only) — search each name variant.
        existing_img_urls = {
            i.get("url") for i in (data.get("images") or []) if isinstance(i, dict)
        }
        for variant in variants:
            for q in render_search_queries("places", "images", name=variant):
                for r in _search_openserp(q, openserp_url):
                    url = r.get("url", "")
                    if not url or url in existing_img_urls:
                        continue
                    if _verify_result(
                        r.get("title", ""),
                        f"Photo of {primary} (WWII place)",
                        grok_client,
                        snippet=r.get("description", ""),
                        url=url,
                    ):
                        data.setdefault("images", []).append(
                            {
                                "url": url,
                                "title": r.get("title", ""),
                                "source": "openserp",
                            }
                        )
                        existing_img_urls.add(url)
                        changed = True

        # Textual sources — fetched + summarized + pos/neg URL cached.
        existing_src_urls = {
            s.get("url") for s in (data.get("web_results") or []) if isinstance(s, dict)
        }
        for variant in variants:
            for q in render_search_queries("places", "sources", name=variant):
                for r in _search_openserp(q, openserp_url):
                    url = r.get("url", "")
                    if not url or url in existing_src_urls:
                        continue
                    processed = process_positive_url(
                        url,
                        r.get("title", ""),
                        r.get("description", ""),
                        f"{primary} in World War II",
                        grok_client,
                    )
                    if processed:
                        data.setdefault("web_results", []).append(processed)
                        existing_src_urls.add(url)
                        _metric("items_added")
                        changed = True

        data["openserp_searched"] = True
        _metric("entities_searched")
        import time as _time

        data["openserp_searched_at"] = int(_time.time())
        if _validate_before_write(data, "places"):
            write_json_with_lock(f, data, entity="places")
        if changed:
            enriched += 1
            _metric("entities_enriched")
            logger.info("  ✓ OpenSERP enriched place: %s", primary)

    logger.info("OpenSERP places enrichment: %d enriched", enriched)
    return enriched
