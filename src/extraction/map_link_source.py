"""In-document map-link ingestion (multimodal gap M1).

HyperWar chapters link author-curated, high-quality maps directly from the narrative, e.g.
``[Map VIII](maps/USA-E-XChannel-VIII.jpg)``. The parser now resolves those links to absolute URLs
into ``parsed.json -> maps[]`` (see parser.extract_maps base_url). This module FOLLOWS each such
link and feeds it into the EXISTING map-as-source machinery:

    link -> SSRF-safe fetch gate (+ same-host/ibiblio allowlist) -> Grok vision verify + `depicted`
         -> global content-hash dedup -> recycle as a map-as-source (contentrepository/ source pair)

It reuses the map_vision prompt (search_external_maps._build_verification_prompt), the vision call
(grok_client.extract_json_with_image), the content-hash dedup (fetch_review._register_content_hash_if_new),
and the map_source_adapter render/recycle — only the DISCOVERY front-end (an in-document link vs an
OpenSERP web search) is new (map spec §3.1 + the OpenSERP map path). Pure of pipeline side effects
beyond writing the source pair; fail-safe (never raises into the phase).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Hosts whose in-document map links we will follow. HyperWar/ibiblio is the curated corpus source;
# same-host-as-the-document is also allowed. Everything else is skipped (defense-in-depth beyond the
# SSRF IP gate — an in-doc link should point at the document's own asset host, not arbitrary sites).
_ALLOWED_MAP_HOSTS = ("ibiblio.org",)


def _ssrf_safe_image_url(url: str):
    """Module-level handle to the shared SSRF image gate (patchable in tests)."""
    from src.extraction.search_external_maps import (
        _ssrf_safe_image_url as _impl,
    )

    return _impl(url)


def _host_allowed(url: str, source_host: str = "") -> bool:
    """True if the map URL's host is the source document's host or an allowlisted corpus host."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if source_host and host == source_host.lower():
        return True
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_MAP_HOSTS)


def _vision_depicted(
    grok_client: Any,
    img_url: str,
    map_title: str,
    context: Dict[str, str],
    image_timeout: int,
) -> Optional[Dict[str, Any]]:
    """Run the map_vision verify+extract on a direct image URL. Returns the `depicted` dict for an
    approved map, or None (rejected / error / not a map). Reuses the shared verification prompt.
    """
    from src.extraction.search_external_maps import _build_verification_prompt

    prompt = _build_verification_prompt(
        context.get("place_name", ""),
        context.get("date"),
        context.get("event_context", ""),
        map_title,
        context.get("alt_text", ""),
        context.get("caption", ""),
    )
    try:
        result = grok_client.extract_json_with_image(
            prompt=prompt,
            image_url=img_url,
            cache_type="in_doc_map_vision",
            image_timeout=image_timeout,
        )
    except Exception as e:  # noqa: BLE001 - a vision error must not crash the ingest
        logger.debug("in-doc map vision errored for %s: %s", img_url[:120], e)
        return None
    if not isinstance(result, dict) or not result.get("is_relevant"):
        return None
    depicted = result.get("depicted")
    return depicted if isinstance(depicted, dict) and depicted else None


def _recycle_as_source(
    map_id: str, map_title: str, img_url: str, depicted: Dict[str, Any]
) -> bool:
    """Write the vision `depicted` as a map-as-source pair via the SHARED adapter recycle (the same
    single writer used by the OpenSERP map path — reuse, one write location). Fail-safe.
    """
    try:
        from src.extraction.map_source_adapter import recycle_map_as_source

        return recycle_map_as_source(
            map_id=map_id, map_title=map_title, source_url=img_url, depicted=depicted
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("OBSERV map_link.error map=%s reason=%s", map_id, str(e)[:120])
        return False


def ingest_in_doc_map_links(
    parsed_docs: List[Dict[str, Any]],
    grok_client: Any,
    *,
    image_timeout: int = 30,
) -> Dict[str, int]:
    """For each parsed doc's ``maps[]`` (in-document map links), follow the link and recycle an
    approved map as a source. Returns a summary dict. Fail-safe; emits OBSERV map_link.* lines.

    LOOP GUARD: a map-derived source (book=map-*) is skipped so an ingested map can't re-trigger this.
    """
    summary = {
        "links": 0,
        "fetched": 0,
        "verified": 0,
        "recycled": 0,
        "skipped": 0,
        "deduped": 0,
    }
    for doc in parsed_docs:
        if not isinstance(doc, dict):
            continue
        book = str(doc.get("book", ""))
        if book.startswith("map-"):
            continue  # loop guard: do not follow a map-source's own map links
        maps = doc.get("maps") or []
        context = _doc_context(doc)
        source_host = urlparse(doc.get("_source_url", "")).hostname or ""
        for m in maps:
            if not isinstance(m, dict):
                continue
            summary["links"] += 1
            _ingest_one_link(
                m, grok_client, context, source_host, image_timeout, summary
            )
    logger.info(
        "OBSERV map_link.summary links=%d fetched=%d verified=%d recycled=%d deduped=%d skipped=%d",
        summary["links"],
        summary["fetched"],
        summary["verified"],
        summary["recycled"],
        summary["deduped"],
        summary["skipped"],
    )
    return summary


def _doc_context(doc: Dict[str, Any]) -> Dict[str, str]:
    """Minimal place/event/date context from the doc to steer map_vision relevance."""
    title = str(doc.get("chapter_title", "") or doc.get("book", ""))
    return {
        "place_name": "",
        "event_context": title,
        "date": "",
        "alt_text": "",
        "caption": title,
    }


def _ingest_one_link(
    m: Dict[str, Any],
    grok_client: Any,
    context: Dict[str, str],
    source_host: str,
    image_timeout: int,
    summary: Dict[str, int],
) -> None:
    """Fetch+verify+recycle one in-document map link (mutates ``summary``). Fail-safe."""
    url = str(m.get("url", ""))
    map_id = str(m.get("map_id", "") or "").strip() or "maplink"
    map_title = str(m.get("description", "") or f"Map {map_id}")
    try:
        cleaned = _ssrf_safe_image_url(url)
        if not cleaned or not _host_allowed(cleaned, source_host):
            logger.info("OBSERV map_link.skipped url=%s reason=ssrf_or_host", url[:120])
            summary["skipped"] += 1
            return
        summary["fetched"] += 1
        depicted = _vision_depicted(
            grok_client, cleaned, map_title, context, image_timeout
        )
        if not depicted:
            logger.info(
                "OBSERV map_link.skipped url=%s reason=not_relevant_map", cleaned[:120]
            )
            summary["skipped"] += 1
            return
        summary["verified"] += 1
        # Global exact-content dedup (reuse the fetch_review registry) keyed by the map URL.
        if not _content_is_new(cleaned):
            logger.info("OBSERV map_link.deduped url=%s", cleaned[:120])
            summary["deduped"] += 1
            return
        if _recycle_as_source(map_id, map_title, cleaned, depicted):
            logger.info(
                "OBSERV map_link.recycled map=%s url=%s -> contentrepository/ "
                "(re-enters pipeline for event derivation)",
                map_id,
                cleaned[:120],
            )
            summary["recycled"] += 1
        else:
            summary["skipped"] += 1
    except Exception as e:  # noqa: BLE001 - one bad link must not crash the ingest
        logger.warning("OBSERV map_link.error map=%s reason=%s", map_id, str(e)[:120])
        summary["skipped"] += 1


def _content_is_new(url: str) -> bool:
    """Dedup an in-doc map by URL via the global content-hash registry (fail-OPEN -> True)."""
    try:
        import hashlib

        from src.enrichment.fetch_review import _register_content_hash_if_new

        digest = "sha256:" + hashlib.sha256(url.encode("utf-8")).hexdigest()
        return _register_content_hash_if_new(digest, f"maplink-{digest[:16]}")
    except Exception:  # noqa: BLE001
        return True
