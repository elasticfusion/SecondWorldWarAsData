"""Intra-corpus document links (M8, multimodal epic — final).

HyperWar chapters cite OTHER in-corpus volumes — "Utah Beach to Cherbourg", "Omaha Beachhead",
"Small Unit Actions" — and cross-reference pages within the same volume ("#page329"). The
deterministic bibliography pass already turns these into bibliography records; what was missing is
(a) recognizing that a cited link IS another document we have or can ingest, marking it as an
INTERNAL corpus-graph edge (with ULID attribution), and (b) owner directive: QUEUE a cited in-corpus
document we don't yet have as ANOTHER BATCH to be processed — through the same content-uploaded front
door an uploaded book uses, so it enters Phase 1->2->3 and joins the corpus.

Reuse-first: the HyperWar recognizer (hyperwar_biblio), the M4 link extractor, the M5
book->SourceSectionID index, the batch front door (contentrepository/_system -> content-uploaded ->
one Phase-1 launch), and the SSRF-safe fetch. Fail-safe; everything observable (OBSERV intra_corpus.*).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _volume_of(url: str) -> Optional[str]:
    """The HyperWar volume key for a URL, or None if it is not a recognizable in-corpus volume page.
    Reuses the authoritative hyperwar_biblio recognizer. Fail-safe."""
    try:
        from src.enrichment.hyperwar_biblio import _parse_volume

        parsed = _parse_volume(url)
        return parsed[0] if parsed else None
    except Exception:  # noqa: BLE001
        return None


def classify_link(url: str, source_url: str) -> str:
    """Classify a resolved link relative to the citing document's ``source_url``:
      ``same_volume_anchor`` — a #fragment or a page in the SAME in-corpus volume (intra-doc nav);
      ``in_corpus_doc``      — a DIFFERENT in-corpus HyperWar volume document (ingestable);
      ``external``           — anything else.
    Pure; fail-safe -> 'external'."""
    try:
        target_vol = _volume_of(url)
        if target_vol is None:
            # a bare same-document "#anchor" is intra-corpus navigation within this volume
            return "same_volume_anchor" if url.startswith("#") else "external"
        src_vol = _volume_of(source_url)
        if src_vol is not None and src_vol == target_vol:
            return "same_volume_anchor"
        return "in_corpus_doc"
    except Exception:  # noqa: BLE001
        return "external"


def _section_volumes(sf: Path) -> set:
    """Volume key(s) a source_section file represents (from source.book / source_url). Fail-safe."""
    import json

    out: set = set()
    try:
        rec = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    src = (rec.get("source") or {}) if isinstance(rec, dict) else {}
    for cand in (src.get("book"), src.get("source_url")):
        v = _volume_of(str(cand or "")) or (str(cand) if cand else "")
        if v:
            out.add(v)
    return out


def _corpus_volumes_present(out: Path) -> set:
    """Volume keys already represented in the corpus (from the source_section records). Used to
    dedup: we don't re-queue a volume we already have. Fail-safe."""
    vols: set = set()
    try:
        sec_dir = out / "source_section"
        if sec_dir.exists():
            for sf in sec_dir.glob("*.json"):
                vols |= _section_volumes(sf)
    except Exception as e:  # noqa: BLE001
        logger.debug("corpus-volumes scan skipped: %s", e)
    return vols


def _edge(
    from_section_id: str, url: str, kind: str, target_vol: Optional[str]
) -> Dict[str, Any]:
    """An internal corpus-graph edge with ULID attribution back to the citing source_section."""
    return {
        "from_SourceSectionID": from_section_id or None,
        "to_url": url,
        "to_volume": target_vol,
        "kind": kind,  # 'same_volume_anchor' | 'in_corpus_doc'
    }


def process_doc_links(
    content: str,
    source_url: str,
    book: str,
    *,
    out: Path,
    book_to_section: Dict[str, str],
    corpus_volumes: set,
    queued: set,
    enqueue: bool = True,
) -> Dict[str, Any]:
    """For one parsed doc: classify every link, record internal corpus-graph edges (ULID-attributed),
    and QUEUE any in-corpus document not already in the corpus / already queued as another batch.
    Returns {edges, anchors, queued, skipped}. Fail-safe."""
    edges: List[Dict[str, Any]] = []
    counts = {"anchors": 0, "queued": 0, "skipped": 0}
    try:
        from src.extraction.link_assets import extract_link_assets

        section_id = book_to_section.get((book or "").strip().lower(), "")
        for la in extract_link_assets(content, source_url):
            kind = classify_link(la.url, source_url)
            if kind == "external":
                continue
            target_vol = _volume_of(la.url)
            edges.append(_edge(section_id, la.url, kind, target_vol))
            if kind == "same_volume_anchor":
                counts["anchors"] += 1
                continue
            # in_corpus_doc -> queue as another batch unless already present/queued
            if _should_queue(target_vol, corpus_volumes, queued):
                if enqueue and _queue_as_batch(la.url, target_vol):
                    queued.add(target_vol)
                    counts["queued"] += 1
                    logger.info(
                        "OBSERV intra_corpus.queued volume=%s url=%s from_section=%s",
                        target_vol,
                        la.url,
                        section_id or "-",
                    )
                else:
                    counts["skipped"] += 1
            else:
                counts["skipped"] += 1
                logger.info(
                    "OBSERV intra_corpus.in_corpus_present volume=%s (already in corpus/queued)",
                    target_vol,
                )
    except Exception as e:  # noqa: BLE001 - must never crash the pipeline
        logger.warning("OBSERV intra_corpus.error reason=%s", str(e)[:120])
    logger.info(
        "OBSERV intra_corpus.summary book=%s edges=%d anchors=%d queued=%d skipped=%d",
        book,
        len(edges),
        counts["anchors"],
        counts["queued"],
        counts["skipped"],
    )
    return {
        "edges": edges,
        "anchors": counts["anchors"],
        "queued": counts["queued"],
        "skipped": counts["skipped"],
    }


def _should_queue(target_vol: Optional[str], corpus_volumes: set, queued: set) -> bool:
    """Queue only a recognizable volume we don't already have and haven't queued this run."""
    return (
        bool(target_vol)
        and target_vol not in corpus_volumes
        and target_vol not in queued
    )


def _fetch_corpus_doc_body(url: str) -> Optional[bytes]:
    """SSRF-safe fetch the in-corpus doc's HTML as bytes, or None (blocked/non-200/empty). Fail-safe."""
    try:
        from src.utils.url_hygiene import fetch_text_safe

        text, status = fetch_text_safe(url)
        if not text:
            logger.info(
                "OBSERV intra_corpus.queue_skipped reason=fetch_%s url=%s", status, url
            )
            return None
        return text.encode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        logger.warning("OBSERV intra_corpus.error url=%s reason=%s", url, str(e)[:120])
        return None


def _upload_batch_item(body: bytes, volume: Optional[str]) -> bool:
    """Content-hash-deduped upload of the doc body to the content-uploaded batch front door
    (contentrepository/_system/...). Reuses the fetch_review S3 client + dedup registry. Fail-safe.
    """
    try:
        from src.enrichment.fetch_review import (
            _register_content_hash_if_new,
            _s3_client_and_bucket,
        )

        client, bucket = _s3_client_and_bucket()
        if client is None:
            logger.info(
                "OBSERV intra_corpus.queue_skipped reason=no_bucket volume=%s", volume
            )
            return False
        digest = hashlib.sha256(body).hexdigest()
        source_id = f"intracorpus-{(volume or 'vol')}-{digest[:12]}"
        if not _register_content_hash_if_new(digest, source_id):
            logger.info(
                "OBSERV intra_corpus.queue_deduped %s volume=%s", digest[:20], volume
            )
            return False
        safe_vol = (
            "".join(c for c in str(volume or "vol") if c.isalnum() or c in "._-")[:60]
            or "vol"
        )
        key = f"contentrepository/_system/{safe_vol}/{source_id}.html"
        client.put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="text/html; charset=utf-8"
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV intra_corpus.error volume=%s reason=%s", volume, str(e)[:120]
        )
        return False


def _queue_as_batch(url: str, volume: Optional[str]) -> bool:
    """QUEUE an in-corpus document as another processing batch: SSRF-safe fetch its HTML, then upload
    to the content-uploaded batch front door so it enters Phase 1->2->3 like an uploaded book.
    Content-hash deduped. Fail-safe; returns True iff queued."""
    body = _fetch_corpus_doc_body(url)
    if body is None:
        return False
    return _upload_batch_item(body, volume)
