"""Bibliography source resolution — find actual documents for citations.

Routes bibliography entries by document type to appropriate search APIs:
  - Military records → NARA catalog (API key required) + Grok record group ID
  - Books → Archive.org, Gutenberg, OpenSERP
  - Journal articles → LOC, OpenSERP
  - Oral histories → LOC Veterans History Project

Each search result is verified by Grok before acceptance.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from src.utils.http_pool import get_session

logger = logging.getLogger(__name__)

# Document types that are military/government records
ARCHIVE_TYPES = {
    "primary source",
    "primary source document",
    "after action report",
    "field order",
    "military message",
    "letter",
    "memo",
    "cable",
    "report",
    "journal",
    "war diary",
}

BOOK_TYPES = {"book", "monograph", "unit history"}
ARTICLE_TYPES = {"journal article", "periodical", "newspaper article"}


def resolve_bibliography_entry(
    entry: Dict[str, Any],
    grok_client: Any = None,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Resolve a single bibliography entry to its source. Modifies in place."""
    if entry.get("search_status") == "resolved" or entry.get("resource_urls"):
        entry["search_status"] = "resolved"
        return entry

    citation = entry.get("citation") or {}
    doc_type = (citation.get("document_type") or "").lower()
    availability = (entry.get("availability") or "").lower()
    cfg = config or {}

    resolver = _pick_resolver(doc_type, availability)
    result = resolver(entry, citation, grok_client, cfg)

    _apply_result(entry, result)
    return entry


def _pick_resolver(doc_type: str, availability: str):
    """Select resolver function by document type."""
    if doc_type in ARCHIVE_TYPES or availability == "archive":
        return _resolve_archive
    if doc_type in BOOK_TYPES or availability == "offline":
        return _resolve_book
    if doc_type in ARTICLE_TYPES:
        return _resolve_article
    return _resolve_generic_entry


def _apply_result(entry: Dict, result: Optional[Tuple]) -> None:
    """Apply resolver result to entry."""
    if not result:
        entry["search_status"] = "not_found"
        return
    url, source, extra = result
    if url:
        entry.setdefault("resource_urls", []).append(url)
        entry["search_source"] = source
        entry["availability"] = "online"
        entry["search_status"] = "resolved"
    elif extra:
        entry.update(extra)
        entry["search_status"] = (
            "resolved" if extra.get("archive_reference_number") else "not_found"
        )
    else:
        entry["search_status"] = "not_found"


def _resolve_archive(entry, citation, grok_client, config):
    """Resolve military/government records."""
    verbatim = entry.get("verbatim_reference", "")
    if not verbatim:
        mentions = entry.get("mentions") or []
        if mentions:
            verbatim = mentions[0].get("verbatim_reference", "")
    return _resolve_archive_record(entry, verbatim, grok_client, config)


def _resolve_book(entry, citation, grok_client, config):
    """Resolve books."""
    title = citation.get("title") or ""
    author = (citation.get("author") or [None])[0]
    result = _resolve_book_search(title, author, citation, grok_client, config)
    if not result:
        alt_title = citation.get("alt_title") or ""
        if alt_title and alt_title != title:
            result = _resolve_book_search(
                alt_title, author, citation, grok_client, config
            )
    return result


def _resolve_article(entry, citation, grok_client, config):
    """Resolve articles."""
    title = citation.get("title") or ""
    author = (citation.get("author") or [None])[0]
    result = _resolve_article_search(title, author, citation, grok_client, config)
    if not result:
        alt_title = citation.get("alt_title") or ""
        if alt_title and alt_title != title:
            result = _resolve_article_search(
                alt_title, author, citation, grok_client, config
            )
    return result


def _resolve_generic_entry(entry, citation, grok_client, config):
    """Generic fallback."""
    title = citation.get("title") or ""
    author = (citation.get("author") or [None])[0]
    result = _resolve_generic(title, author, grok_client, config)
    if not result:
        alt_title = citation.get("alt_title") or ""
        if alt_title and alt_title != title:
            result = _resolve_generic(alt_title, author, grok_client, config)
    return result


# --- Archive/Military Records ---


# Keywords indicating US or captured German military documents held at NARA
_NARA_INDICATORS = {
    "rg",
    "record group",
    "nara",
    "national archives",
    "ag file",
    "g-3",
    "g-2",
    "g-4",
    "shaef",
    "etousa",
    "fusa",
    "first army",
    "third army",
    "twelfth army",
    "army group",
    "after action",
    "aar",
    "field order",
    "operations order",
    "war diary",
    "jnl",
    "journal",
    "msg file",
    "cable",
    "ocmh",
    "wdss",
    "hist sec",
    "historical division",
    "ob west",
    "okw",
    "okh",
    "panzer",
    "armee",
    "heeresgruppe",
    "wehrmacht",
    "luftwaffe",
    "kriegstagebuch",
    " inf ",
    " div ",
    " fo ",
    "unit rpt",
    "unit report",
    "arty",
    "cav",
    "engr",
    "recon",
    "bn",
    "regt",
}


def _is_nara_searchable(entry: Dict, verbatim: str) -> bool:
    """Check if this entry is likely a US/German military document at NARA."""
    # Already has a NARA record group reference
    ref = entry.get("archive_reference_number", "")
    if ref and ref != "None" and "RG" in ref.upper():
        return True
    # Check verbatim, title, and archive_ref for NARA indicators
    citation = entry.get("citation") or {}
    text = " ".join(
        [
            verbatim,
            citation.get("title", ""),
            ref if ref and ref != "None" else "",
        ]
    ).lower()
    if not text.strip():
        return False
    return any(indicator in text for indicator in _NARA_INDICATORS)


def _resolve_archive_record(
    entry: Dict, verbatim: str, grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Resolve military/government records via Grok + NARA."""
    citation = entry.get("citation") or {}
    title = citation.get("title", "")
    search_text = verbatim or title

    extra = _identify_record_group(entry, search_text, grok_client)
    url = _search_online_sources(
        entry, verbatim, search_text, title, grok_client, config
    )
    if url:
        return (url[0], url[1], extra)
    return (None, "nara_grok", extra) if extra else None


def _identify_record_group(entry: Dict, search_text: str, grok_client: Any) -> Dict:
    """Ask Grok to identify NARA Record Group if not already known."""
    extra = {}
    current_ref = entry.get("archive_reference_number") or ""
    if current_ref == "None":
        current_ref = ""
    has_rg = "RG" in current_ref.upper() if current_ref else False

    if not has_rg and grok_client and search_text:
        ref = _identify_nara_record(search_text, grok_client)
        if ref:
            full_ref = f"{ref}, {current_ref}" if current_ref else ref
            extra["archive_reference_number"] = full_ref
            extra["archive_physical_address"] = (
                "National Archives and Records Administration (NARA), "
                "College Park, MD 20740, USA"
            )
    return extra


def _search_online_sources(
    entry: Dict,
    verbatim: str,
    search_text: str,
    title: str,
    grok_client: Any,
    config: Dict,
) -> Optional[Tuple[str, str]]:
    """Search NARA, OpenSERP, Archive.org, LOC for online copies."""
    # 1. NARA catalog
    nara_key = config.get("nara_api_key")
    if nara_key and _is_nara_searchable(entry, verbatim):
        ref = entry.get("archive_reference_number") or ""
        if ref and ref != "None":
            # Extract just the RG number (e.g., "RG 407, Entry 427") — strip Grok commentary
            rg_short = ref.split("\n")[0].strip()[:50]
            # Combine RG with verbatim for specific search
            query = f"{verbatim[:60]} {rg_short}" if verbatim else rg_short
        else:
            query = verbatim[:100] or title
        url = _search_nara(query, nara_key, grok_client, verbatim)
        if url:
            return (url, "nara_catalog")
        # Retry with alt_title if available
        alt_title = (entry.get("citation") or {}).get("alt_title") or ""
        if alt_title and alt_title != query:
            url = _search_nara(alt_title[:100], nara_key, grok_client, verbatim)
            if url:
                return (url, "nara_catalog")

    # 2. OpenSERP for digitized copies (HathiTrust, university sites, etc.)
    if config.get("use_openserp"):
        openserp_url = config.get("openserp_url", "http://localhost:7001")
        current_ref = entry.get("archive_reference_number") or ""
        ref = current_ref if current_ref and current_ref != "None" else title
        url = _search_openserp_archive(ref, openserp_url, grok_client, entry)
        if url:
            return (url, "openserp_archive")

    # 3. Archive.org
    from src.extraction.supplemental_search import search_archive_org

    url = search_archive_org(title)
    if url:
        return (url, "archive_org")

    return None


_openserp_down = False


def _search_openserp_archive(
    ref: str, openserp_url: str, grok_client: Any = None, entry: Dict = None
) -> Optional[str]:
    """Search OpenSERP for digitized copies of archive documents."""
    global _openserp_down
    if _openserp_down:
        return None

    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("openserp_archive", ref)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    try:
        import time

        time.sleep(5)
        session = get_session()
        resp = session.get(
            f"{openserp_url}/mega/search",
            params={
                "text": f"{ref} digitized document",
                "limit": "5",
                "mode": "any",
                "engines": "google,bing,duckduckgo",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            for r in results:
                url = r.get("url", "")
                if not url:
                    continue
                result_title = r.get("title", "")
                if grok_client and not _verify_openserp_match(
                    ref, result_title, url, grok_client, entry
                ):
                    logger.debug(
                        "  ✗ OpenSERP rejected: '%s' for query '%s'",
                        result_title[:60],
                        ref[:60],
                    )
                    continue
                cache_result("openserp_archive", ref, url)
                logger.info("Found online copy: %s", url)
                return url
    except Exception as e:
        logger.warning("OpenSERP unreachable, disabling for this run: %s", e)
        _openserp_down = True
        return None

    cache_result("openserp_archive", ref, None)
    return None


def _verify_openserp_match(
    query: str, result_title: str, url: str, grok_client: Any, entry: Dict = None
) -> bool:
    """Verify an OpenSERP result is relevant to the specific citation, not a random document.

    Equipment searches are allowed to match photographs, but must be of the correct equipment.
    All other searches must match the specific event/unit/document cited.
    """
    doc_type = ""
    if entry:
        citation = entry.get("citation") or {}
        doc_type = (citation.get("document_type") or "").lower()

    is_equipment = doc_type in {"equipment", "photograph", "image"}

    if is_equipment:
        verify_prompt = f"""Does this search result show an accurate photograph of the specific equipment cited?

Citation: "{query[:300]}"
Search result title: "{result_title[:300]}"
URL: {url}

Return ONLY "YES" or "NO"."""
    else:
        verify_prompt = f"""Does this search result match the specific document/event being searched for?

Citation searched: "{query[:300]}"
Search result title: "{result_title[:300]}"
URL: {url}

Return ONLY "YES" or "NO"."""

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt("bibliography_verify", verify_prompt=verify_prompt)

    try:
        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You verify whether search results match specific military document citations. Be strict.",
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_verify",
        )
        accepted = response.strip().upper().startswith("YES")
        logger.info(
            "Verify result: %s | query=%s | title=%s | url=%s",
            "ACCEPT" if accepted else "REJECT",
            query[:80],
            result_title[:80],
            url[:100],
        )
        return accepted
    except Exception:
        return False


def _identify_nara_record(verbatim: str, grok_client: Any) -> Optional[str]:
    """Use Grok to identify the NARA Record Group for a citation."""
    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt("nara_identify", verbatim=verbatim)

    try:
        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You are a NARA archivist specializing in WWII military records.",
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_nara",
        )
        ref = response.strip().strip('"')
        if ref and ref != "UNKNOWN" and "RG" in ref:
            logger.info("NARA identify: FOUND %s | citation=%s", ref, verbatim[:80])
            return ref
        logger.info("NARA identify: NOT FOUND | citation=%s", verbatim[:80])
    except Exception as e:
        logger.debug("NARA identification failed: %s", e)
    return None


def _search_nara(
    query: str, api_key: str, grok_client: Any, verbatim: str
) -> Optional[str]:
    """Search NARA catalog API v2."""
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("nara", query)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    try:
        session = get_session()
        logger.debug("NARA query: %s", query[:100])
        resp = session.get(
            "https://catalog.archives.gov/api/v2/records/search",
            params={"q": query, "limit": "5"},
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.debug("NARA API returned %d", resp.status_code)
            return None

        results = resp.json().get("body", {}).get("hits", {}).get("hits", [])
        logger.debug("NARA returned %d results", len(results))
        for hit in results:
            source = hit.get("_source", {})
            record = source.get("record", source)
            title = record.get("title", source.get("title", ""))
            nara_id = record.get("naId", source.get("naId", ""))
            logger.debug("  Candidate: %s (naId=%s)", title[:80], nara_id)
            if not nara_id:
                continue
            url = f"https://catalog.archives.gov/id/{nara_id}"
            if grok_client and not _verify_nara_match(query, title, grok_client):
                logger.debug(
                    "  ✗ Rejected: '%s' does not match query '%s'",
                    title[:60],
                    query[:60],
                )
                continue
            logger.debug("  ✓ Accepted: %s", url)
            cache_result("nara", query, url)
            return url
    except Exception as e:
        logger.debug("NARA search failed: %s", e)
    cache_result("nara", query, None)
    return None


# --- Books ---


def _resolve_book_search(
    title: str, author: Optional[str], citation: Dict, grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Resolve books via Archive.org, Gutenberg, OpenSERP."""
    from src.extraction.supplemental_search import (
        search_archive_org,
        search_gutenberg_openserp,
        search_openserp,
    )

    # Archive.org
    url = search_archive_org(title, author)
    if url and _verify_match(title, url, grok_client):
        return (url, "archive_org", None)

    # Gutenberg
    openserp_url = config.get("openserp_url", "http://localhost:7001")
    if config.get("search_gutenberg", True):
        url = search_gutenberg_openserp(title, author, openserp_url)
        if url and _verify_match(title, url, grok_client):
            return (url, "gutenberg", None)

    # OpenSERP general
    if config.get("use_openserp", False):
        url = search_openserp(title, author, openserp_url)
        if url and _verify_match(title, url, grok_client):
            return (url, "openserp", None)

    return None


# --- Articles ---


def _resolve_article_search(
    title: str, author: Optional[str], citation: Dict, grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Resolve journal articles."""
    from src.extraction.supplemental_search import search_archive_org

    url = search_archive_org(title, author)
    if url and _verify_match(title, url, grok_client):
        return (url, "archive_org", None)
    return None


# --- Generic ---


def _resolve_generic(
    title: str, author: Optional[str], grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Generic search fallback."""
    from src.extraction.supplemental_search import search_archive_org

    url = search_archive_org(title, author)
    if url:
        return (url, "archive_org", None)
    return None


def _verify_nara_match(query: str, nara_title: str, grok_client: Any) -> bool:
    """Verify a NARA catalog result actually matches the searched citation.

    Checks that the specific unit, document type, and time period align.
    A generic WWII document from the wrong unit is NOT a match.
    """
    from src.utils.prompt_loader import render_prompt as _rp

    prompt = _rp(
        "nara_verify", citation=query, nara_title=nara_title, nara_description=""
    )

    try:
        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You verify whether NARA catalog records match specific military document citations. Be strict about unit identity.",
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_verify",
        )
        return response.strip().upper().startswith("YES")
    except Exception:
        return False  # Reject on failure rather than accept wrong documents


# --- Verification ---


def _verify_match(candidate_title: str, verbatim: str, grok_client: Any) -> bool:
    """Use Grok to verify a search result matches the citation (title + content check).

    Args:
        candidate_title: The title of the cited work being searched for.
        verbatim: The URL found by search, or the original citation text.
        grok_client: Grok API client for verification.
    """
    if not grok_client:
        return True

    # Determine if verbatim is a URL (from search) or citation text
    url = verbatim if verbatim.startswith("http") else None
    citation_text = candidate_title

    # Quick title-based check (only if we don't have a URL to verify)
    if not url:
        verify_prompt = f"""Does this search result match the citation?

Citation: "{candidate_title[:200]}"
Result: "{verbatim[:200]}"

Return ONLY "YES" or "NO"."""
        from src.utils.prompt_loader import render_prompt as _rp2

        prompt = _rp2("bibliography_verify", verify_prompt=verify_prompt)
        try:
            response = grok_client.chat_completion(
                prompt=prompt,
                system_prompt="You verify whether search results match citations.",
                temperature=0.0,
                use_cache=True,
                cache_type="bibliography_verify",
            )
            accepted = response.strip().upper().startswith("YES")
            logger.info(
                "Verify title: %s | citation=%s | result=%s",
                "ACCEPT" if accepted else "REJECT",
                candidate_title[:80],
                verbatim[:80],
            )
            return accepted
        except Exception:
            return True

    # URL found — verify page content matches the citation
    return _verify_url_content(url, citation_text, grok_client)


def _verify_url_content(url: str, citation: str, grok_client: Any) -> bool:
    """Fetch first ~2000 chars of a URL and ask Grok if it matches the citation."""
    try:
        session = get_session()
        resp = session.get(url, timeout=10, headers={"User-Agent": "WWII-Pipeline/2.2"})
        if resp.status_code != 200:
            return False

        # Extract text content (strip HTML if needed)
        content = resp.text[:4000]
        if "<html" in content.lower():
            from html2text import html2text

            content = html2text(content)[:2000]
        else:
            content = content[:2000]

        verify_prompt = f"""Does this web page content match the cited document?

Citation: "{citation[:300]}"

Page content (first 2000 chars):
{content}

Return ONLY "YES" or "NO". Answer "NO" if the page is unrelated, a generic search page, a paywall, or doesn't contain the cited document."""
        from src.utils.prompt_loader import render_prompt as _rp3

        prompt = _rp3("bibliography_verify", verify_prompt=verify_prompt)

        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You verify whether web page content matches a bibliography citation.",
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_verify",
        )
        match = response.strip().upper().startswith("YES")
        logger.info(
            "Verify URL content: %s | url=%s | citation=%s",
            "ACCEPT" if match else "REJECT",
            url[:100],
            citation[:80],
        )
        return match
    except Exception as e:
        logger.debug("URL content verification failed for %s: %s", url[:80], e)
        return True  # Accept on failure to avoid blocking


# --- Batch Processing ---


def resolve_bibliography_dir(
    bib_dir: Path,
    grok_client: Any = None,
    config: Optional[Dict] = None,
    max_items: Optional[int] = None,
) -> Dict[str, int]:
    """Resolve all unresolved bibliography entries in a directory.

    Returns stats: {resolved, not_found, skipped, errors}
    """
    stats = {"resolved": 0, "not_found": 0, "skipped": 0, "errors": 0}
    skip_files = {"index.json", "review_queue.json"}

    files = sorted(bib_dir.glob("*.json"))
    processed = 0

    for f in files:
        if f.name in skip_files:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            stats["errors"] += 1
            continue

        if data.get("search_status") == "resolved":
            stats["skipped"] += 1
            continue

        if max_items and processed >= max_items:
            break

        try:
            resolve_bibliography_entry(data, grok_client, config)
            from src.schemas import inject_metadata

            inject_metadata(data)
            f.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if data.get("search_status") == "resolved":
                stats["resolved"] += 1
            else:
                stats["not_found"] += 1
        except Exception as e:
            logger.warning("Failed to resolve %s: %s", f.name, e)
            stats["errors"] += 1

        processed += 1

    logger.info(
        "Bibliography resolution: %d resolved, %d not found, %d skipped, %d errors",
        stats["resolved"],
        stats["not_found"],
        stats["skipped"],
        stats["errors"],
    )
    return stats
