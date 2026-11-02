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


def persist_map_entities(
    parsed_docs: List[Dict[str, Any]],
    maps_dir: Path,
    download: bool = True,
    output_root: Optional[Path] = None,
) -> Dict[str, int]:
    """Step 5a — persist a Map ENTITY per in-document map, keyed by URL, ACCUMULATING each chapter's
    reference footprint (roman-numeral campaign foldouts are shared across chapters/books). Reuses
    map_common.find_or_create_map_by_url (URL find-or-create + append) + base_map_record. Distinct
    from ingest_in_doc_map_links (which recycles a map as a SOURCE document): this writes the Map
    entity record + its cross-chapter references[]. Fail-safe; emits OBSERV maps.persist_summary.
    The url_index persists in maps_dir/index.json so accumulation survives across docs AND runs.
    """
    maps_dir.mkdir(parents=True, exist_ok=True)
    index_path = maps_dir / "index.json"
    url_index = _load_url_index(index_path)
    counts = {"created": 0, "existing": 0, "skipped": 0}
    for doc in parsed_docs:
        if not isinstance(doc, dict):
            continue
        book = str(doc.get("book", ""))
        if book.startswith("map-"):
            continue  # loop guard (mirror ingest_in_doc_map_links)
        for m in doc.get("maps") or []:
            if isinstance(m, dict):
                _persist_one_map(
                    m, book, maps_dir, url_index, counts, download, output_root
                )
    _save_url_index(index_path, url_index)
    logger.info(
        "OBSERV maps.persist_summary created=%d existing=%d skipped=%d status=%s",
        counts["created"],
        counts["existing"],
        counts["skipped"],
        "warn" if counts["skipped"] else "ok",
    )
    return counts


def _load_url_index(index_path: Path) -> Dict[str, str]:
    import json as _json

    if not index_path.exists():
        return {}
    try:
        return _json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a corrupt index is loud, rebuilt empty
        logger.warning(
            "OBSERV maps.index_read_failed reason=%s status=warn", type(e).__name__
        )
        return {}


def _save_url_index(index_path: Path, url_index: Dict[str, str]) -> None:
    # Sanctioned index write (matches places/dates/batch_parallel) — NOT a raw write_text.
    from src.utils.file_lock import write_json_with_lock

    try:
        write_json_with_lock(index_path, url_index)
    except Exception as e:  # noqa: BLE001 - index write failure is loud, not fatal
        logger.warning(
            "OBSERV maps.index_write_failed reason=%s status=warn", type(e).__name__
        )


def _persist_one_map(
    m: Dict[str, Any],
    book: str,
    maps_dir: Path,
    url_index: Dict[str, str],
    counts: Dict[str, int],
    download: bool = True,
    output_root: Optional[Path] = None,
) -> None:
    """Persist (find-or-create + append footprint) one in-document map. Mutates counts."""
    from src.extraction.map_common import (
        base_map_record,
        find_or_create_map_by_url,
        classify_map_type,
        download_map_image,
    )

    url = str(m.get("url", "") or "").strip()
    if not url:
        counts["skipped"] += 1
        return
    refs = _stamp_book(m.get("references") or [], book)
    before = url in url_index

    title = str(m.get("description", "") or m.get("map_id", ""))

    def _build(map_id: str) -> Dict[str, Any]:
        # Runs ONLY on CREATE (find_or_create calls build_record just for a new map) -> download +
        # classify happen once per map, never on a cross-chapter append (idempotent).
        local_image_path = file_format = None
        if download:
            local_image_path, file_format = download_map_image(
                url, map_id, maps_dir / "files"
            )
        return {
            **base_map_record(
                map_id=map_id,
                map_title=title,
                description=m.get("description"),
                map_type=classify_map_type(title),
                source_url=url,
                local_path=None,
                local_image_path=local_image_path,
                file_format=file_format,
                storage_backend="filesystem",
            ),
            "figure_number": m.get("map_id"),
        }

    f = find_or_create_map_by_url(
        url=url,
        maps_dir=maps_dir,
        url_index=url_index,
        references=refs,
        build_record=_build,
    )
    if f is None:
        counts["skipped"] += 1
        return
    if before:
        counts["existing"] += 1
    else:
        counts["created"] += 1
    # Step 5c-i: deterministic Place/Date/Organization linking from the footprint sub-events.
    if output_root is not None:
        _link_persisted_map(f, output_root)


def _link_persisted_map(map_file: Path, output_root: Path) -> None:
    """Read the persisted map, stamp Place/Date/Org links from its footprint, write back if changed.
    Fail-safe."""
    import json as _json

    from src.extraction.map_common import link_map_entities
    from src.utils.file_lock import write_json_with_lock

    try:
        rec = _json.loads(map_file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - never block the phase on a link read
        logger.warning(
            "OBSERV maps.link_read_failed file=%s reason=%s status=warn",
            map_file.name,
            type(e).__name__,
        )
        return
    before = (
        rec.get("PlaceMentionID"),
        rec.get("DateMentionID"),
        rec.get("PeopleGroupID"),
    )
    link_map_entities(rec, output_root)
    after = (
        rec.get("PlaceMentionID"),
        rec.get("DateMentionID"),
        rec.get("PeopleGroupID"),
    )
    if after != before:
        write_json_with_lock(map_file, rec)


def _stamp_book(references: List[Dict[str, Any]], book: str) -> List[Dict[str, Any]]:
    """Stamp the source `book` onto each reference footprint entry (cross-chapter/-book attribution
    + the dedup key). Preserves paragraph_number (mandatory) + Sub_eventID."""
    out = []
    for r in references:
        if not isinstance(r, dict):
            continue
        rr = dict(r)
        rr.setdefault("book", book)
        out.append(rr)
    return out
