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
from src.schemas import inject_metadata

logger = logging.getLogger(__name__)


def _source_award_citations(person_data: Dict[str, Any]) -> None:
    """Fill authoritative award citations + provenance for people in an award
    context, routed by AWARDING POWER through the YAML source registry
    (config/award_sources.yaml). Opt-in via AWARD_CITATIONS_ENABLED (default off;
    makes polite network calls). Behaviour:

      * each award routes to the record system of the power that issued it
        (e.g. an Iron Cross -> German sources even for a French recipient);
      * only ENABLED, implemented adapters run; disabled/offline sources no-op;
      * every fetch is polite (per-host crawl-delay) + cached + the fetched page is
        PRESERVED as a primary-source record (local/S3);
      * non-English citations are TRANSLATED to English (original + language kept).

    Fail-safe: any error leaves awards unchanged.
    """
    import os

    if os.getenv("AWARD_CITATIONS_ENABLED", "false").lower() != "true":
        return
    try:
        from src.enrichment.award_registry import make_selector
        from src.enrichment.award_sources import (
            enrich_person_awards,
            should_source_awards,
        )

        if not should_source_awards(person_data):
            return
        storage = _award_storage()
        selector = make_selector(storage=storage)
        n = enrich_person_awards(person_data, selector)
        if n:
            logger.info("  ✓ Sourced %d authoritative award citation(s)", n)
        # Persist any errored attempts durably (S3/local) for later retry + UI review.
        _persist_award_errors(person_data, storage)
    except Exception as e:  # noqa: BLE001 - enrichment extra; never block the person
        logger.warning("Award-citation sourcing skipped: %s", e)


def _derive_and_validate_title_memberships(
    person_data: Dict[str, Any], grok_client
) -> None:
    """Derive civilian group memberships from title/aliases (born date-unverified), then
    OPTIONALLY run the deliberate Grok temporal validation to confirm them. The
    validation is gated by TITLE_MEMBERSHIP_VALIDATE=true (makes Grok calls). Deriving
    is always safe (nothing is asserted as fact until verified). Fail-safe."""
    import os

    try:
        from src.extraction.title_memberships import (
            enrich_title_memberships,
            validate_membership_dates,
        )

        added = enrich_title_memberships(person_data)
        if added:
            logger.info(
                "  ✓ Derived %d title-implied membership(s) [date-unverified]", added
            )
        if (
            os.getenv("TITLE_MEMBERSHIP_VALIDATE", "false").lower() == "true"
            and grok_client is not None
        ):
            v = validate_membership_dates(person_data, grok_client)
            if v:
                logger.info("  ✓ Temporal-validated %d membership(s)", v)
    except Exception as e:  # noqa: BLE001 - never block the person
        logger.warning("Title-membership enrichment skipped: %s", e)


def _persist_award_errors(person_data: Dict[str, Any], storage) -> None:
    """Write any errored sourcing attempts to the durable error log (fail-safe)."""
    if storage is None:
        return
    try:
        _persist_award_attempts(person_data, storage)
    except Exception as e:  # noqa: BLE001
        logger.debug("award error persistence skipped: %s", e)


def _persist_award_attempts(person_data: Dict[str, Any], storage) -> None:
    """Persist each award's sourcing_attempts to the durable error log. Extracted
    verbatim from _persist_award_errors' try-body — same field fallbacks (get-based,
    no hard index), same skip of non-dict awards, same persist call. Exceptions
    propagate to the caller's fail-safe handler (unchanged)."""
    from src.enrichment.award_source_pages import persist_sourcing_errors

    bp = person_data.get("biographical_profile") or {}
    awards = bp.get("military_awards") or person_data.get("military_awards") or []
    pid = person_data.get("PersonID") or person_data.get("person_id") or ""
    pname = person_data.get("name") or person_data.get("current_name") or ""
    for award in awards:
        _persist_one_award_attempt(persist_sourcing_errors, storage, pid, pname, award)


def _persist_one_award_attempt(persist_sourcing_errors, storage, pid, pname, award):
    """Persist a single award's sourcing_attempts (skip non-dict awards / empty
    attempts). Extracted verbatim from _persist_award_attempts' loop body — same
    get-based field access (no hard index), same skip semantics."""
    if not isinstance(award, dict):
        return
    attempts = award.get("sourcing_attempts")
    if attempts:
        persist_sourcing_errors(storage, pid, pname, award.get("award", ""), attempts)


def _award_storage():
    """Build the storage backend for award-page preservation (local/S3) from config.
    Returns None on any failure (preservation is best-effort, never fatal)."""
    import os

    try:
        from src.utils.backends import create_storage
        from src.utils.config import load_config

        config = load_config()
        base = Path(os.getenv("AWARD_PAGES_DIR", "output"))
        return create_storage(config, base)
    except Exception as e:  # noqa: BLE001
        logger.debug("award page storage unavailable (%s); pages not preserved", e)
        return None


_URL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}


def search_grokipedia(
    person_name: str, timeout: int = 30, max_retries: int = 2
) -> Optional[str]:
    """Search Grokipedia for person biographical data."""
    from src.utils.search_cache import get_cached

    cached = get_cached("grokipedia", person_name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    for attempt in range(max_retries):
        try:
            result = _grokipedia_query_scan(person_name, timeout)
            return result

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


def _grokipedia_query_scan(person_name: str, timeout: int) -> Optional[str]:
    """Run the Grokipedia search queries for one attempt; on a name-matched result
    write the positive cache + slug and return the page HTML, else write the NEGATIVE
    cache and return None. Extracted verbatim from search_grokipedia's try-body —
    requests.Timeout / other exceptions are NOT caught here so the caller preserves
    the TRANSIENT (no cache, retry) vs NEGATIVE (cache_result None) distinction."""
    from src.utils.search_cache import cache_result

    last_name, first_initial = _name_match_tokens(person_name)
    first_initial = first_initial.lower()
    search_queries = [
        f"{person_name} World War",
        f"{last_name} World War",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }

    for search_q in search_queries:
        search_url = f"https://grokipedia.com/search?q={search_q}"
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        response = recorded_fetch(
            "grokipedia",
            search_url,
            kind=TEXTUAL,
            method="GET",
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )
        if response.status_code != 200 or "/page/" not in response.text:
            continue
        slug = _match_grokipedia_page(response.text, last_name.lower(), first_initial)
        if slug:
            cache_result("grokipedia", person_name, response.text)
            _grokipedia_slugs[person_name] = slug
            return response.text

    # No match found in any search query
    cache_result("grokipedia", person_name, None)
    return None


def _match_grokipedia_page(
    html: str, last_name_lower: str, first_initial: str
) -> Optional[str]:
    """Scan Grokipedia search-result HTML for a slug whose name matches (last name present +
    first-initial agreement) and whose snippet is military-relevant. Returns the slug or None.
    """
    import re as _re

    results = _re.findall(
        r'data-slug="([^"]+)"[^>]*data-search-result-link="true"[^>]*data-scroll-anchor-text="([^"]*)"',
        html,
    )
    for slug, snippet in results:
        slug_lower = slug.lower().replace("_", " ")
        if last_name_lower not in slug_lower:
            continue
        if first_initial and slug_lower.split()[0][0] != first_initial:
            continue
        # Short snippets may lack context — only enforce military relevance for longer ones.
        if len(snippet) > 50 and not _is_military_relevant(snippet):
            continue
        return slug
    return None


def _build_wikipedia_request(person_name: str) -> tuple[str, dict, dict]:
    """Build Wikipedia API request parameters. Returns (url, params, headers)."""
    api_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "format": "json",
        "titles": person_name,
        "prop": "extracts|pageimages",
        "exintro": "True",
        "explaintext": "True",
        "redirects": "1",
        "piprop": "original",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
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


def _extract_page_image(response_data: dict) -> Optional[str]:
    """Extract primary image URL from Wikipedia API response."""
    pages = response_data.get("query", {}).get("pages", {})

    for page_id, page_data in pages.items():
        if page_id != "-1":
            return page_data.get("original", {}).get("source")

    return None


def _extract_page_image_filename(response_data: dict) -> Optional[str]:
    """Extract the pageimage filename for license lookup."""
    pages = response_data.get("query", {}).get("pages", {})

    for page_id, page_data in pages.items():
        if page_id != "-1":
            # Try pageimage field first, fall back to extracting from URL
            filename = page_data.get("pageimage")
            if filename:
                return filename
            url = page_data.get("original", {}).get("source", "")
            if url:
                return url.rsplit("/", 1)[-1]

    return None


def _fetch_image_license(filename: str) -> Optional[str]:
    """Fetch license info for a Wikipedia/Commons image file."""
    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "wikimedia-commons",
            "https://commons.wikimedia.org/w/api.php",
            kind=TEXTUAL,
            method="GET",
            params={
                "action": "query",
                "format": "json",
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "extmetadata",
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            },
            timeout=10,
            result_summary_fn=lambda r: f"status={r.status_code} file={filename[:40]!r}",
        )
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for _page_id, page_data in pages.items():
                ii = page_data.get("imageinfo", [])
                if ii:
                    meta = ii[0].get("extmetadata", {})
                    return meta.get("LicenseShortName", {}).get("value", "unknown")
    except Exception as e:
        logger.debug("Failed to fetch image license for %s: %s", filename, e)
    return None


# Module-level store for Wikipedia images found during search
_wikipedia_images: dict = {}

# Grokipedia slug for each person (used to hint Wikipedia lookup)
_grokipedia_slugs: dict = {}


def get_wikipedia_image(person_name: str) -> Optional[dict]:
    """Get cached Wikipedia image info for a person (found during search_wikipedia).

    Returns dict with 'url', 'license', and (when preserved) 'preserved_path' keys,
    or None.
    """
    return _wikipedia_images.get(person_name)


def _store_wikipedia_image(person_name: str, response_json: dict) -> None:
    """Record a person's Wikipedia portrait (url + license) AND preserve the photo
    itself to storage (S3/local) as a retained source record — same principle as the
    award source pages. Fail-safe: any error leaves the text enrichment unaffected."""
    img_url = _extract_page_image(response_json)
    if not img_url:
        return
    filename = _extract_page_image_filename(response_json)
    license_info = _fetch_image_license(filename) if filename else None
    info = {"url": img_url, "license": license_info or "unknown"}
    # Download + preserve the actual photo bytes.
    try:
        preserved = _preserve_person_photo(person_name, img_url)
        if preserved:
            info["preserved_path"] = preserved
    except Exception as e:  # noqa: BLE001 - preservation best-effort, never fatal
        logger.debug("Wikipedia photo preservation skipped for %s: %s", person_name, e)
    _wikipedia_images[person_name] = info


def _preserve_person_photo(person_name: str, img_url: str) -> Optional[str]:
    """Fetch the portrait bytes (polite UA) and store them to the configured storage
    backend as a retained record under person_photos/. Returns the stored path."""
    from src.enrichment.award_source_pages import preserve_page
    from src.utils.http_pool import browser_headers

    storage = _award_storage()  # reuse the configured storage backend (local/S3)
    if storage is None:
        return None
    # img_url is UNTRUSTED (Grokipedia/Wikipedia response), so route through safe_get (SSRF-safe:
    # clean_url + DNS-rebind resolve-check + no redirects + byte cap), preserving .content/.headers.
    from src.utils.url_hygiene import safe_get

    resp = safe_get(img_url, headers=browser_headers(), timeout=30)
    if resp is None:
        logger.info(
            "OBSERV bio.photo_blocked ssrf_or_fetch_fail url=%s", str(img_url)[:120]
        )
        return None
    if resp.status_code != 200 or not resp.content:
        logger.debug("photo fetch %s -> HTTP %s", img_url, resp.status_code)
        return None
    content_type = resp.headers.get("Content-Type", "image/jpeg")
    person_slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in person_name)[
        :60
    ]
    return preserve_page(
        storage,
        person_slug,
        img_url,
        resp.content,
        content_type,
        prefix="person_photos",
    )


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


_MILITARY_KEYWORDS = frozenset(
    [
        "military",
        "army",
        "navy",
        "general",
        "colonel",
        "major",
        "captain",
        "lieutenant",
        "brigadier",
        "world war",
        "wwii",
        "division",
        "regiment",
        "battalion",
        "infantry",
        "armor",
        "artillery",
        "combat",
        "campaign",
        "luftwaffe",
        "wehrmacht",
        "panzer",
        "allied",
        "soldier",
        "officer",
    ]
)


def _is_military_relevant(text: str) -> bool:
    """Check if Wikipedia extract is about a military figure."""
    lower = text.lower()
    return sum(1 for kw in _MILITARY_KEYWORDS if kw in lower) >= 2


def _search_wikipedia_fallback(
    person_name: str, headers: dict, timeout: int
) -> Optional[str]:
    """Fall back to Wikipedia search API when direct title lookup misses."""
    try:
        last_name, first_initial = _name_match_tokens(person_name)
        search_queries = [
            f'"{person_name}" "World War"',
            f'{last_name} "World War" {" ".join(p[0] for p in person_name.split() if p[0].isupper() and len(p) <= 2)}',
        ]

        for srsearch in search_queries:
            import time as _time

            _time.sleep(3)  # Rate limit between Wikipedia API calls
            from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

            resp = recorded_fetch(
                "wikipedia",
                "https://en.wikipedia.org/w/api.php",
                kind=TEXTUAL,
                method="GET",
                params={
                    "action": "query",
                    "format": "json",
                    "list": "search",
                    "srsearch": srsearch,
                    "srlimit": "3",
                },
                headers=headers,
                timeout=timeout,
                result_summary_fn=lambda r: f"search status={r.status_code}",
            )
            if resp.status_code != 200:
                return None
            results = resp.json().get("query", {}).get("search", [])
            if not results:
                continue
            content = _wiki_fallback_match(
                results, person_name, last_name, first_initial, headers, timeout
            )
            if content:
                return content
    except Exception as e:
        logger.debug("Wikipedia search fallback failed for %s: %s", person_name, e)
    return None


def _name_match_tokens(person_name: str) -> "tuple[str, str]":
    """Return (last_name, first_initial) used to match a candidate title/slug to the person.
    last_name = last 'real' name part (len>2, not an abbreviation); first_initial = first
    capitalized token's initial."""
    name_parts = [p for p in person_name.split() if len(p) > 2 and not p.endswith(".")]
    last_name = name_parts[-1] if name_parts else person_name
    first_initial = ""
    for p in person_name.split():
        if p and p[0].isupper():
            first_initial = p[0]
            break
    return last_name, first_initial


def _wiki_fallback_match(
    results: "list",
    person_name: str,
    last_name: str,
    first_initial: str,
    headers: dict,
    timeout: int,
) -> Optional[str]:
    """For each Wikipedia search hit, require last-name + first-initial agreement, then fetch the
    intro extract and keep it if military-relevant. Returns the content or None."""
    for result in results:
        title = result.get("title", "")
        if not last_name or last_name.lower() not in title.lower():
            continue
        if first_initial:
            title_words = title.split()
            if title_words and title_words[0][0].upper() != first_initial.upper():
                continue
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp2 = recorded_fetch(
            "wikipedia",
            "https://en.wikipedia.org/w/api.php",
            kind=TEXTUAL,
            method="GET",
            params={
                "action": "query",
                "format": "json",
                "titles": result["title"],
                "prop": "extracts|pageimages",
                "exintro": "True",
                "explaintext": "True",
                "redirects": "1",
                "piprop": "original",
            },
            headers=headers,
            timeout=timeout,
            result_summary_fn=lambda r: f"extract status={r.status_code}",
        )
        if resp2.status_code == 200:
            content = _extract_page_content(resp2.json())
            if content and _is_military_relevant(content):
                _store_wikipedia_image(person_name, resp2.json())
                return content
    return None


def search_wikipedia(
    person_name: str, timeout: int = 30, max_retries: int = 2
) -> Optional[str]:
    """Search Wikipedia for person biographical data."""
    _wikipedia_rate_limit()

    from src.utils.search_cache import get_cached

    cached = get_cached("wikipedia", person_name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    # If Grokipedia already found a page, use slug as Wikipedia title hint
    grok_info = get_wikipedia_image(person_name)  # check if already populated
    grok_slug = _grokipedia_slugs.get(person_name)
    if grok_slug:
        # Convert "albin_f_irzyk" → "Albin F Irzyk" for targeted lookup
        wiki_title = grok_slug.replace("_", " ").title()
        logger.debug(
            "Wikipedia: using Grokipedia hint '%s' for %s", wiki_title, person_name
        )
    else:
        wiki_title = person_name

    api_url, params, headers = _build_wikipedia_request(wiki_title)

    for attempt in range(max_retries):
        try:
            from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

            response = recorded_fetch(
                "wikipedia",
                api_url,
                kind=TEXTUAL,
                method="GET",
                params=params,
                headers=headers,
                timeout=timeout,
                result_summary_fn=lambda r: f"status={r.status_code}",
            )

            if response.status_code == 200:
                return _handle_wikipedia_200(person_name, response, timeout)

            if response.status_code == 429:
                import time

                retry_after = int(response.headers.get("retry-after", "15"))
                logger.debug(
                    "Wikipedia rate limited for %s, waiting %ds",
                    person_name,
                    retry_after,
                )
                time.sleep(retry_after)
                continue  # Retry

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

    # Exhausted retries without success — don't cache (likely rate limited)
    return None


def _wikipedia_rate_limit() -> None:
    """Global rate limit: max 1 Wikipedia request per 5 seconds across all threads.
    Extracted verbatim from search_wikipedia's prelude (same lock/_last_call attrs
    on search_wikipedia, same 5s window)."""
    import threading
    import time

    if not hasattr(search_wikipedia, "_lock"):
        search_wikipedia._lock = threading.Lock()  # type: ignore[attr-defined]
        search_wikipedia._last_call = 0.0  # type: ignore[attr-defined]
    with search_wikipedia._lock:  # type: ignore[attr-defined]
        elapsed = time.time() - search_wikipedia._last_call  # type: ignore[attr-defined]
        if elapsed < 5:
            time.sleep(5 - elapsed)
        search_wikipedia._last_call = time.time()  # type: ignore[attr-defined]


def _handle_wikipedia_200(person_name: str, response, timeout: int) -> Optional[str]:
    """Handle a 200 Wikipedia API response: extract content, preserve the photo
    (image + license), cache, and fall back to the search API on a direct-title
    miss. Extracted verbatim from search_wikipedia — the photo-preserve path
    (_store_wikipedia_image) and BOTH cache_result writes are unchanged."""
    from src.utils.search_cache import cache_result

    _, _, headers = _build_wikipedia_request(person_name)
    content = _extract_page_content(response.json())
    # Store image + license + preserve the photo itself.
    _store_wikipedia_image(person_name, response.json())
    if content:
        cache_result("wikipedia", person_name, content)
        return content
    # Direct title miss — try search API
    content = _search_wikipedia_fallback(person_name, headers, timeout)
    cache_result("wikipedia", person_name, content)
    return content


def extract_biographical_data(
    person_name: str,
    source_text: str,
    source_name: str,
    grok_client: GrokClient,
    max_retries: int = 2,
) -> Optional[Dict[str, Any]]:
    """Extract structured biographical data from source text using Grok."""
    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt(
        "biography",
        person_name=person_name,
        source_name=source_name,
        text=source_text[:5000],
    )

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


def _fetch_url_content(
    url: str,
    timeout: int = 15,
    provenance: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Fetch URL content. Returns text or None on failure.

    If the live page is blocked by a bot-protection/WAF layer (Incapsula/Cloudflare/
    etc. — often served as an HTTP-200 *block page*) or is unreachable, transparently
    falls back to the Internet Archive Wayback Machine (politely rate-limited) and
    returns the archived HTML when a usable snapshot exists. When a caller passes a
    ``provenance`` dict and the archive is used, it is populated with
    ``source`` = "wayback", ``archived_url`` and ``wayback_capture_timestamp`` so the
    record can truthfully reflect that content came from the archive, not the live site.
    """
    from src.enrichment import wayback

    live_html: Optional[str] = None
    status: Optional[int] = None
    # url is UNTRUSTED (Grok-derived source_urls), so route the LIVE fetch through fetch_text_safe
    # (SSRF-safe: clean_url + DNS-rebind resolve-check + no redirects + byte cap) rather than a bare
    # requests.get(allow_redirects=True). A blocked/failed live fetch falls through to the Wayback
    # fallback (archive.org, a trusted literal host) exactly as a WAF-block/unreachable did before.
    from src.utils.url_hygiene import fetch_text_safe

    live_html, status = fetch_text_safe(url, timeout=timeout, headers=_URL_HEADERS)
    if status is None and live_html is None:
        logger.info("OBSERV bio.url_blocked ssrf_or_fetch_fail url=%s", str(url)[:120])
    elif status is not None and status != 200:
        logger.debug("  URL returned %d: %s", status, url)
        live_html = None

    # Live content is good only if it is not a WAF/bot-block page.
    if live_html is not None and not wayback.is_block_page(live_html, status):
        return live_html

    # Blocked or unreachable -> try the Wayback Machine (polite, rate-limited).
    recovered = wayback.fetch_archived(url, timeout=max(timeout, 20))
    if recovered is not None:
        html, archived_url, ts = recovered
        if provenance is not None:
            provenance["source"] = "wayback"
            provenance["archived_url"] = archived_url
            provenance["wayback_capture_timestamp"] = ts
        return html
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
            relevant = bool(result.get("relevant"))
            logger.info(
                "Verify bio URL: %s | person=%s | url=%s | reason=%s",
                "ACCEPT" if relevant else "REJECT",
                person_name[:40],
                url[:100],
                result.get("reason", "")[:80],
            )
            return relevant
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
    from src.enrichment.enrichment_gate import should_re_search

    return should_re_search(data)


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
        from src.utils.file_lock import write_json_with_lock

        write_json_with_lock(person_file, person_data, entity="people")
        return False

    person_data["biographical_profile"] = bio_profile
    person_data["enrichment_status"] = "enriched"
    person_data["last_enrichment_search"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )

    # Authoritative award citations (US personnel in an award context): fill
    # verbatim citation text + provenance from Hall of Valor (direct, polite,
    # cached) — preferred over the search-engine valor path. Fail-safe: any error
    # leaves awards as-is. See AWARD_SOURCING.md.
    _source_award_citations(person_data)

    # Civilian group memberships implied by title/aliases (House/state/party). Born
    # date-UNVERIFIED (point-in-time honorific); a deliberate, opt-in Grok temporal
    # validation confirms them before they are asserted as fact. Fail-safe.
    _derive_and_validate_title_memberships(person_data, grok_client)

    try:
        from src.extraction.people import (
            Person,
        )  # pylint: disable=import-outside-toplevel

        Person(**person_data)
    except Exception as e:
        logger.error("  ❌ Validation failed for %s: %s", person_name, e)
        return False

    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(person_file, person_data, entity="people")
    logger.info("  ✅ Enriched %s", person_name)
    return True


def _is_already_enriched(person_data: Dict[str, Any]) -> bool:
    """Gate: True if this person should be SKIPPED (already enriched or a fresh
    not_found). Mirrors the skip logic in ``_load_person_for_enrichment`` so the
    in-place and file-based paths gate identically.
    """
    bio_profile = person_data.get("biographical_profile") or {}
    if bio_profile.get("birth_date") or bio_profile.get("biographical_details"):
        return True
    if person_data.get("enrichment_status") == "not_found" and not _should_re_search(
        person_data
    ):
        return True
    return False


def enrich_person_from_sources(person_data: Dict[str, Any], grok_client) -> bool:
    """Enrich ONE person record from Grokipedia/Wikipedia IN PLACE (Phase 2).

    Augments ``person_data`` with: Grokipedia bio + Wikipedia bio (with search
    fallback) + Wikipedia portrait image (preserved to storage) + authoritative award
    citations + title-implied memberships. Returns True if anything changed.

    Does **no file I/O** on person files — the caller (the Phase-2 people extractor)
    owns the save path (``write_json_with_lock``). Reuses all the existing fetch /
    caching helpers.

    GATED: skips (returns False) when the record is already enriched or is a fresh
    ``not_found`` (same staleness logic as ``enrich_all_people`` via
    ``_load_person_for_enrichment`` / ``_should_re_search``). On a genuine
    ``BatchModeCollecting`` the request is collected and False is returned (not marked
    not_found).
    """
    person_name = person_data.get("name", "")
    if not person_name:
        return False

    if _is_already_enriched(person_data):
        logger.debug("  Already enriched / fresh not_found: %s, skipping", person_name)
        return False

    bio_profile = person_data.get("biographical_profile") or {}
    logger.info("Enriching (phase 2): %s", person_name)

    try:
        enriched = _run_enrichment_steps(
            person_name, bio_profile, grok_client, search_references_flag=True
        )
    except BatchModeCollecting:
        # Batch mode — request collected; leave record untouched (don't mark not_found).
        raise

    if not enriched:
        person_data["enrichment_status"] = "not_found"
        person_data["last_enrichment_search"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d"
        )
        return True

    person_data["biographical_profile"] = bio_profile
    person_data["enrichment_status"] = "enriched"
    person_data["last_enrichment_search"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )

    # Authoritative award citations (opt-in via AWARD_CITATIONS_ENABLED). Fail-safe.
    _source_award_citations(person_data)

    # Title/alias-implied civilian memberships (date-unverified; opt-in Grok temporal
    # validation via TITLE_MEMBERSHIP_VALIDATE). Fail-safe.
    _derive_and_validate_title_memberships(person_data, grok_client)

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
        "nationality_served",
        "role_type",
        "primary_group_id",
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
    """Find a people group file for a unit name.

    Fast path: exact case-insensitive name match. Otherwise match via the canonical
    unit key (number + service + arm + echelon, nickname-resolved) so that
    '9th Division' links to a group stored as '9th Infantry Division', and
    'Screaming Eagles' links to the '101st Airborne Division' — the SAME matcher the
    group deduper uses. Service/arm/echelon/number vetoes still apply (never a false link).
    """
    # Fast path: exact name.
    for key, filename in group_index.items():
        if key.lower() == unit_name.lower():
            path = groups_dir / filename
            if path.exists():
                return path

    # Canonical-key path (unify linker with deduper).
    try:
        from src.dedup.unit_key import derive_unit_key, unit_keys_match

        uk = derive_unit_key(unit_name)
        if not uk.numbers:
            return None  # no number -> can't canonical-key match (avoid false links)
        for key, filename in group_index.items():
            gk = derive_unit_key(key)
            if not gk.numbers:
                continue
            matched, _ = unit_keys_match(uk, gk)
            if matched:
                path = groups_dir / filename
                if path.exists():
                    return path
    except Exception:  # noqa: BLE001 - linking is best-effort; never block enrichment
        return None
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

    member = {
        "PersonID": person_id,
        "name": person_name,
        "role": unit.get("role", "Member"),
        "from_date": unit.get("from"),
        "to_date": unit.get("to"),
        "source": "biographical_enrichment",
        "confidence": 0.8,
    }
    # Civilian memberships carry provenance so the group side knows an unverified
    # title membership is PROVISIONAL (not a confirmed member).
    for extra in ("membership_kind", "date_verified", "implied_from_title"):
        if extra in unit:
            member[extra] = unit[extra]
    # A date-unverified membership is lower confidence on the group side.
    if unit.get("date_verified") is False and unit.get("implied_from_title"):
        member["confidence"] = 0.3
    members.append(member)

    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(group_file, group_data, entity="people_groups")
    return True


def _link_person_to_groups(
    person_file: Path, people_groups_dir: Path, grok_client: Any = None
) -> int:
    """Add person as member to matching people groups based on units_served."""
    if not people_groups_dir.exists():
        return 0

    with open(person_file, "r", encoding="utf-8") as f:
        person_data = json.load(f)

    bio = person_data.get("biographical_profile", {})
    units = bio.get("units_served", [])
    affiliations = bio.get("group_affiliations", [])
    if not units and not affiliations:
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

    linked = _link_units(
        units,
        group_index,
        people_groups_dir,
        person_id,
        person_name,
        grok_client=grok_client,
        person_data=person_data,
    )
    linked += _link_affiliations(
        affiliations, group_index, people_groups_dir, person_id, person_name
    )
    return linked


def _validate_and_canonicalize_unit(
    grok_client: Any, unit_name: str
) -> Optional[Dict[str, str]]:
    """LLM validity + canonicalization gate before minting a group from a person's unit.

    A person's ``units_served`` entry is a free-text string ("4th Division"); we only create a
    group for it if Grok confirms it is a REAL, identifiable military formation and returns a
    canonical name. Returns ``{"canonical": <name>, "echelon": <echelon|"">}`` or ``None`` when
    unverifiable / unknown (fail-safe: on any error or UNKNOWN we do NOT create — null-over-fake).
    """
    try:
        prompt = (
            "You are validating a World War II military unit reference for a database. "
            f'Is "{unit_name}" a real, identifiable military formation (not a vague/garbled '
            "fragment)? If YES, reply with exactly one line of JSON: "
            '{"valid": true, "canonical_name": "<full canonical unit name>", '
            '"echelon": "<division|regiment|battalion|company|corps|army|brigade|"">"}. '
            'If it is NOT a real identifiable unit, reply exactly: {"valid": false}. '
            "Reply with ONLY the JSON, no prose."
        )
        raw = grok_client.chat_completion(prompt).strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, dict) or not parsed.get("valid"):
            return None
        canonical = str(parsed.get("canonical_name") or "").strip()
        if not canonical:
            return None
        return {
            "canonical": canonical,
            "echelon": str(parsed.get("echelon") or "").strip(),
        }
    except Exception:  # noqa: BLE001 - validation is best-effort; never block linking
        return None


def _provisional_group_event_mentions(person_data: Dict[str, Any]) -> list:
    """Carry the person's event/source lineage onto a minted group so the group is traceable
    back to WHERE it came from ("track the event source"). Fresh MentionID per item; the
    EventID/Sub_eventID references are preserved verbatim (never re-minted)."""
    from ulid import new as new_ulid

    out = []
    for em in person_data.get("event_mentions", []) or []:
        if not isinstance(em, dict):
            continue
        # Build omitting falsy keys: the GROUPS event_mentions schema makes EventID NON-nullable,
        # while a person's EventID is nullable — so a None/absent reference must be OMITTED, never
        # written as null (the item has no `required` list, so omission is schema-valid).
        item: Dict[str, Any] = {
            "MentionID": str(new_ulid()),
            "context": "Unit membership derived from a person's service record",
        }
        for key in (
            "EventID",
            "Event_Name",
            "Sub_eventID",
            "Sub_event_Name",
            "book",
            "author",
            "series",
        ):
            val = em.get(key)
            if val:
                item[key] = val
        out.append(item)
    return out


def _create_provisional_group(
    unit_name: str,
    canon: Dict[str, str],
    group_index: Dict[str, str],
    people_groups_dir: Path,
    person_data: Dict[str, Any],
) -> Optional[Path]:
    """Mint a PROVISIONAL military-unit people_group derived from a person's unit affiliation.

    Born ``enrichment_status="ambiguous"`` with an ``enrichment_ambiguity_reason`` recording that
    it was bootstrapped from a single person (not independently extracted), and carrying the
    person's event/source lineage in ``event_mentions`` so dedup/Phase-3 can confirm + enrich it.
    Persistence (GroupID mint, merge-vs-create, index.json, write_json_with_lock) is delegated to
    the shared ``people_groups._save_group`` — this helper only assembles the provisional record.
    Returns the new/merged file path (or None on failure).
    """
    try:
        from src.extraction.people_groups import (
            _name_to_filename,
            _normalize_name,
            _save_group,
        )

        canonical = canon["canonical"]
        person_name = person_data.get("name", "")
        ems = _provisional_group_event_mentions(person_data)
        first_em = ems[0] if ems else {}
        group: Dict[str, Any] = {
            "group_name": canonical,
            "name": canonical,
            "group_type": "military_unit",
            "description": (
                f"Military unit, provisionally created from the service record of "
                f"{person_name or 'a person'} (source reference: \"{unit_name}\"). "
                "Not yet independently extracted from a narrative source."
            ),
            "members": [],
            "event_mentions": ems,
            "enrichment_status": "ambiguous",
            "enrichment_ambiguity_reason": (
                "provisional: bootstrapped from a person's units_served (LLM-validated as a real "
                f'formation, canonicalized from "{unit_name}"); awaiting narrative extraction / '
                "dedup confirmation"
            ),
        }
        if canon.get("echelon"):
            group["military_hierarchy"] = {"echelon": canon["echelon"]}

        index_file = people_groups_dir / "index.json"
        # Reuse the canonical persist path (mints GroupID, merges-or-creates, updates index,
        # schema-validated write, stamps book/author/series onto event_mentions from the source).
        _save_group(
            people_groups_dir,
            index_file,
            group,
            first_em.get("book") or "",
            first_em.get("author") or "",
            first_em.get("series") or "",
        )
        # Resolve the file _save_group wrote/merged, and keep this run's in-memory index hot.
        return _resolve_saved_group_path(
            canonical, group, group_index, people_groups_dir, unit_name, person_data
        )
    except Exception as e:  # noqa: BLE001 - best-effort; never block linking
        logger.debug("provisional group create skipped for %r: %s", unit_name, e)
        return None


def _resolve_saved_group_path(
    canonical, group, group_index, people_groups_dir, unit_name, person_data
) -> Optional[Path]:
    """Resolve the path _save_group wrote (via index.json), update the in-memory index, and
    verify the file actually exists (a blocked write must NOT yield a phantom path)."""
    from src.extraction.people_groups import _name_to_filename, _normalize_name

    index_file = people_groups_dir / "index.json"
    norm = _normalize_name(canonical)
    filename = None
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            filename = json.load(f).get(norm)
    if not filename:
        filename = _name_to_filename(canonical, str(group.get("GroupID", "")))
    group_index[norm] = filename
    created_path = people_groups_dir / filename
    if not created_path.exists():
        # _save_group's write was blocked (schema guard). Do NOT hand back a phantom path —
        # that would make _add_member_to_group open() a nonexistent file and crash linking.
        logger.warning(
            "OBSERV group.provisional_create_blocked unit=%r (write not persisted)",
            unit_name,
        )
        return None
    logger.info(
        "OBSERV group.provisional_created unit=%r canonical=%r derived_from_person=%s",
        unit_name,
        canonical,
        person_data.get("PersonID", ""),
    )
    return created_path


def _resolve_or_create_group_file(
    unit_name, group_index, people_groups_dir, grok_client, person_data
):
    """Find the matching group file; if none and creation is enabled (grok_client+person_data
    both present), LLM-validate the unit and mint a provisional group. Returns a path or None.
    """
    group_file = _find_group_file(unit_name, group_index, people_groups_dir)
    if group_file or grok_client is None or person_data is None:
        return group_file
    canon = _validate_and_canonicalize_unit(grok_client, unit_name)
    if not canon:
        return None
    return _create_provisional_group(
        unit_name, canon, group_index, people_groups_dir, person_data
    )


def _link_units(
    units,
    group_index,
    people_groups_dir,
    person_id,
    person_name,
    grok_client=None,
    person_data=None,
) -> int:
    """Link a person to each matching military-unit group (by unit key). Returns count linked.

    When no group matches AND provisional creation is enabled (env ``PROVISIONAL_GROUPS=true``)
    and a grok_client is available, LLM-validate the unit and mint a provisional group, then link
    into it — so a person's stated unit no longer silently fails to connect (coverage gap close).
    """
    import os

    create_missing = (
        os.getenv("PROVISIONAL_GROUPS", "false").lower() == "true"
        and grok_client is not None
        and person_data is not None
    )
    linked = 0
    for unit in units:
        unit_name = unit.get("unit", "")
        if not unit_name:
            continue
        group_file = _resolve_or_create_group_file(
            unit_name,
            group_index,
            people_groups_dir,
            grok_client if create_missing else None,
            person_data if create_missing else None,
        )
        if not group_file:
            continue
        try:
            added = _add_member_to_group(group_file, person_id, person_name, unit)
        except (
            Exception
        ):  # noqa: BLE001 - one bad group file must not abort linking others
            logger.debug("skip member-link for %r (group read/write failed)", unit_name)
            continue
        if added:
            linked += 1
            logger.info("  Linked %s → %s", person_name, unit_name)
    return linked


def _link_affiliations(
    affiliations, group_index, people_groups_dir, person_id, person_name
) -> int:
    """Link a person to civilian group memberships (legislature/state/party, incl. title-implied).
    The membership's date_verified flag travels onto the member record (unverified title
    membership -> PROVISIONAL member). Returns count linked."""
    linked = 0
    for aff in affiliations:
        if not isinstance(aff, dict):
            continue
        gname = aff.get("group", "")
        if not gname:
            continue
        group_file = _find_group_file_by_name(gname, group_index, people_groups_dir)
        if not group_file:
            continue
        member = {
            "role": "Member",
            "from": aff.get("as_of_source_date"),
            "membership_kind": aff.get("group_kind"),
            "date_verified": bool(aff.get("date_verified")),
            "implied_from_title": bool(aff.get("implied_from_title")),
        }
        if _add_member_to_group(group_file, person_id, person_name, member):
            linked += 1
            tag = "verified" if member["date_verified"] else "provisional"
            logger.info("  Linked %s → %s [%s]", person_name, gname, tag)
    return linked


def _find_group_file_by_name(name, group_index, groups_dir):
    """Find a group file for a civilian membership by exact case-insensitive name.
    (Civilian org names are not unit-key resolvable; GroupID-based reconciliation is
    handled by dedup/merge, not here.)"""
    for key, filename in group_index.items():
        if key.lower() == (name or "").lower():
            p = groups_dir / filename
            if p.exists():
                return p
    return None


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

    enriched = _enrich_people_parallel(
        person_files, grok_client, search_references_flag, max_workers
    )

    # Link enriched people to people groups
    _link_people_to_groups(person_files, people_dir, grok_client)

    logger.info("=" * 60)
    logger.info(
        "Enrichment complete: %d/%d people enriched", enriched, len(person_files)
    )

    return enriched


def _enrich_people_parallel(
    person_files: list,
    grok_client: GrokClient,
    search_references_flag: bool,
    max_workers: int,
) -> int:
    """Enrich all person files in a thread pool; return the enriched count.
    Extracted verbatim from enrich_all_people — same Heartbeat lifecycle, same
    per-future error handling and progress logging."""
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
    return enriched


def _link_people_to_groups(
    person_files: list, people_dir: Path, grok_client: Any = None
) -> None:
    """Link enriched people to people-group files (if the dir exists). Extracted
    verbatim from enrich_all_people — same path, same logging."""
    people_groups_dir = people_dir.parent / "people_groups"
    if people_groups_dir.exists():
        logger.info("Linking people to groups...")
        groups_linked = 0
        for person_file in person_files:
            groups_linked += _link_person_to_groups(
                person_file, people_groups_dir, grok_client
            )
        if groups_linked:
            logger.info("Linked %d person-group membership(s)", groups_linked)


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
