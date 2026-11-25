"""Extract + recognize ALL outbound links in a parsed document (M4, multimodal epic).

HyperWar-style pages cite external assets through ordinary links — a footnote to a Medal-of-Honor
citation page (``../../MoH_P-T.html#roosevelt``), an in-page award anchor (``#pinder``), a command
chart image (``charts/USA-E-XChannel-4.jpg``), scanned reports (``*.pdf``), archival footage
(``*.mp4``), etc. Nothing downstream FOLLOWS these. This module is step one of closing that gap: it
pulls every link out of the parsed markdown, resolves it against the source document's URL
(relative paths + ``#anchor`` fragments), and RECOGNIZES the target's media type by reusing the
ingestion front-end's detector (:mod:`src.ingestion.media_detection`) — html / image / pdf / tif
(-> image) / video (-> moving_image) / docx / epub / text / unsupported.

Recognition is deterministic and offline (extension-based); it performs NO network I/O — a link is
only FETCHED later, after a human affirms it in review (see image_person_link's sibling enqueue
path). Pure of side effects; fail-safe (never raises into the pipeline).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urljoin, urlparse

from src.grok_client import BatchModeCollecting
from src.ingestion.media_detection import _from_extension
from src.utils.batch_pending import Pending
from src.utils.url_hygiene import has_url_junk
from src.ingestion.source_metadata import MediaType

logger = logging.getLogger(__name__)

from src.utils.observe import observe_warn  # noqa: E402

# [text](target) — ordinary markdown links. Images (![...]) are handled by the M2/M3 image path, so
# exclude a leading '!' via a negative lookbehind. target stops at the first ')' or whitespace.
_MD_LINK = re.compile(r"(?<!\!)\[([^\]]*)\]\(([^)\s]+)\)")
# Bare HTML anchors that survive conversion: <a href="...">text</a>.
_HTML_HREF = re.compile(
    r"<a\s+[^>]*href=[\"']([^\"'>]+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL
)


@dataclass
class LinkAsset:
    """A recognized outbound link: resolved URL + its link text + detected media type."""

    url: str  # resolved (absolute, or a same-doc "#anchor")
    text: str  # the link's anchor text
    media_type: MediaType  # media_detection verdict (image/html/pdf/moving_image/.../unsupported)
    is_anchor: bool  # True for a same-document #fragment (intra-corpus navigation)


def _recognize_media_type(url: str) -> MediaType:
    """Detect the link target's media type from its path extension (offline). ``unsupported`` when
    no extension matches (e.g. an extensionless page or a bare ``#anchor``)."""
    try:
        path = urlparse(url).path or url
        return _from_extension(Path(path)) or "unsupported"
    except Exception:  # noqa: BLE001
        return "unsupported"


def _resolve_target(target: str, base_url: str) -> Optional[tuple]:
    """Resolve a raw link target against ``base_url``. Returns (resolved_url, is_anchor) or None to
    skip (empty / unusable). A pure ``#fragment`` is same-document navigation (kept as the fragment
    when there is no base). Fail-safe."""
    try:
        t = (target or "").strip()
        if has_url_junk(t):
            # malformed (line break, control character, inner space): rejected, not repaired
            logger.info("OBSERV link_asset.malformed url=%r status=skipped", t[:200])
            return None
        if not t or t.startswith(("mailto:", "javascript:", "tel:")):
            return None
        if t.startswith("#"):
            return (t, True)  # same-document anchor
        if t.startswith(("http://", "https://")):
            return (t, False)
        # relative path (optionally with its own #frag) -> resolve against the source doc URL
        if base_url:
            resolved = urljoin(base_url, t)
            if resolved.startswith(("http://", "https://")):
                return (resolved, False)
        return None
    except Exception:  # noqa: BLE001
        return None


def extract_link_assets(content: str, base_url: str = "") -> List[LinkAsset]:
    """Extract + recognize every outbound link in ``content`` (markdown + surviving HTML anchors),
    resolved against ``base_url`` (the source document URL), deduped by resolved URL. Pure;
    fail-safe -> [] on any error."""
    out: List[LinkAsset] = []
    try:
        seen: set = set()
        for text, target in _iter_raw_links(content):
            resolved = _resolve_target(target, base_url)
            if resolved is None:
                continue
            url, is_anchor = resolved
            if url in seen:
                continue
            seen.add(url)
            mt = "unsupported" if is_anchor else _recognize_media_type(url)
            out.append(
                LinkAsset(
                    url=url, text=text.strip(), media_type=mt, is_anchor=is_anchor
                )
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("extract_link_assets skipped: %s", e)
    return out


def _iter_raw_links(content: str):
    """Yield (text, target) for each markdown link and HTML anchor in ``content``."""
    for m in _MD_LINK.finditer(content or ""):
        yield m.group(1), m.group(2)
    for m in _HTML_HREF.finditer(content or ""):
        # group(1)=href, group(2)=inner text (strip any nested tags for a clean label)
        yield re.sub(r"<[^>]+>", "", m.group(2)), m.group(1)


def enqueue_doc_links(
    content: str,
    base_url: str,
    book: str,
    *,
    grok_client: Any = None,
    whitelist: Optional[List[str]] = None,
    blacklist: Optional[List[str]] = None,
    source_section_id: str = "",
) -> dict:
    """Extract + recognize every link in one parsed doc, relevance-gate each, and enqueue the kept
    ones for HUMAN review (fetch_provenance TEXTUAL PENDING, provider=``link-asset``) carrying the
    recognized media type + relevancy verdict + provenance. ``source_section_id`` is the ULID of the
    originating source_section, carried on the event so an affirmed asset's eventual entity link is
    ULID-attributed (M6 chart ingest already reads origin_SourceSectionID from here). Same-document
    ``#anchor`` navigation is NOT enqueued. Returns a summary. Fail-safe.
    """
    summary = {
        "links": 0,
        "enqueued": 0,
        "already_queued": 0,
        "dropped": 0,
        "undecided": 0,
        "failed": 0,
        "anchors": 0,
        "in_corpus": 0,
    }
    pending = Pending()
    try:
        from src.enrichment.source_retrieval import load_blacklist
        from src.extraction.link_relevancy import should_enqueue_link

        wl = whitelist if whitelist is not None else None
        bl = (
            blacklist if blacklist is not None else _safe_load_blacklist(load_blacklist)
        )

        def judge(la: "LinkAsset"):
            return should_enqueue_link(
                la.url,
                la.text,
                context=book,
                grok_client=grok_client,
                whitelist=wl,
                blacklist=bl,
            )

        from src.extraction.intra_corpus import classify_link

        for la in extract_link_assets(content, base_url):
            summary["links"] += 1
            if la.is_anchor:
                summary["anchors"] += 1
                continue
            if classify_link(la.url, base_url) != "external":
                # another HyperWar volume, or a page of this one: indexed by intra_corpus_links
                # only (owner decision, TODO Group 9), never judged by Grok or queued here too
                summary["in_corpus"] += 1
                continue
            _handle_link(la, book, base_url, source_section_id, summary, pending, judge)
    except Exception as e:  # noqa: BLE001 - enqueue must never crash the pipeline
        observe_warn("link_asset", "error", reason=str(e)[:120])
    if pending:
        # batch rounds: the summary is not final (queued links are neither enqueued nor dropped)
        pending.raise_if_queued()
    logger.info(
        "OBSERV link_asset.summary book=%s links=%d enqueued=%d already_queued=%d dropped=%d "
        "undecided=%d failed=%d anchors=%d in_corpus=%d",
        book,
        summary["links"],
        summary["enqueued"],
        summary["already_queued"],
        summary["dropped"],
        summary["undecided"],
        summary["failed"],
        summary["anchors"],
        summary["in_corpus"],
    )
    return summary


def _safe_load_blacklist(loader) -> List[str]:
    """Load the license-rejection blacklist; [] on any error (fail-safe)."""
    try:
        return loader()
    except Exception:  # noqa: BLE001
        return []


def _handle_link(
    la: "LinkAsset",
    book: str,
    base_url: str,
    source_section_id: str,
    summary: dict,
    pending: Any,
    judge: Any,
) -> None:
    """One link: skip it if an earlier run recorded it (no Grok request), else have Grok judge it
    and queue it only when relevant."""
    if _already_queued(la, book, base_url, source_section_id):
        summary["already_queued"] += 1
        return
    try:
        enqueue, verdict = judge(la)
    except BatchModeCollecting as e:
        # relevancy QUEUED for the batch: neither enqueued for review nor dropped; the next pass
        # has the verdict. Every link queues its own request.
        pending.note(e)
        return
    if verdict == "undecided":
        # Grok could not say (UNKNOWN, an unusable reply, an error, no client): not queued and
        # nothing recorded, so a later run asks again
        summary["undecided"] += 1
        return
    if not enqueue:
        summary["dropped"] += 1
        logger.info("OBSERV link_asset.dropped verdict=%s url=%r", verdict, la.url)
        return
    _enqueue_one(la, book, base_url, verdict, source_section_id, summary)


def _origin(book: str, source_url: str, source_section_id: str):
    from src.utils import find_queue

    return find_queue.Origin(
        entity_type="source_section" if source_section_id else "",
        entity_id=source_section_id,
        book=book,
        source_url=source_url,
    )


def _legacy_key(la: "LinkAsset", book: str, source_url: str, ssid: str) -> tuple:
    """The once_key releases before the shared queue used for the same link."""
    return (book, ssid or source_url, la.url)


def _already_queued(
    la: "LinkAsset", book: str, source_url: str, source_section_id: str
) -> bool:
    from src.utils import find_queue

    return find_queue.already_queued(
        "link-asset",
        la.url,
        _origin(book, source_url, source_section_id),
        _legacy_key(la, book, source_url, source_section_id),
    )


def _enqueue_one(
    la: "LinkAsset",
    book: str,
    source_url: str,
    verdict: str,
    source_section_id: str,
    summary: dict,
) -> None:
    """Queue one Grok-approved link through the shared ``find_queue.enqueue_find`` (once per link per
    source section; a link a human rejected or submitted is never queued again). Carries the
    originating source_section ULID (``origin_SourceSectionID``) so an affirmed asset's eventual
    entity link is ULID-attributed, and whether the link is to a trusted source (for the reviewer).
    Fail-safe."""
    from src.extraction.link_relevancy import is_whitelisted
    from src.utils import find_queue

    try:
        status = find_queue.enqueue_find(
            provider="link-asset",
            url=la.url,
            verdict=verdict,
            origin=_origin(book, source_url, source_section_id),
            legacy_key=_legacy_key(la, book, source_url, source_section_id),
            summary=f"{la.media_type} link ({verdict}) from {book} <{source_url}>: {la.text[:80]}",
            params={
                "media_type": la.media_type,
                "link_text": la.text[:200],
                "origin_SourceSectionID": source_section_id or None,
                "trusted_source": is_whitelisted(la.url),
            },
        )
        if status == find_queue.REFUSED:
            summary["dropped"] += 1
            return
        if status == find_queue.FAILED:
            summary[
                "failed"
            ] += 1  # already logged as an error by find_queue; tried again next run
            return
        if status != find_queue.ENQUEUED:
            summary["already_queued"] += 1
            return
        summary["enqueued"] += 1
        logger.info(
            "OBSERV link_asset.enqueued media=%s verdict=%s source_url=%r source_section=%s url=%r",
            la.media_type,
            verdict,
            source_url,
            source_section_id or "-",
            la.url,
        )
    except Exception as e:  # noqa: BLE001
        observe_warn("link_asset", "error", url=la.url, reason=str(e)[:120])
