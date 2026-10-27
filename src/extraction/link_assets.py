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

from src.ingestion.media_detection import _from_extension
from src.ingestion.source_metadata import MediaType

logger = logging.getLogger(__name__)

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
) -> dict:
    """Extract + recognize every link in one parsed doc, relevance-gate each, and enqueue the kept
    ones for HUMAN review (fetch_provenance TEXTUAL PENDING, provider=``link-asset``) carrying the
    recognized media type + relevancy verdict + provenance. Same-document ``#anchor`` navigation is
    NOT enqueued (intra-corpus concern, not a new external asset). Returns a summary. Fail-safe.
    """
    summary = {"links": 0, "enqueued": 0, "dropped": 0, "anchors": 0}
    try:
        from src.enrichment.source_retrieval import load_blacklist
        from src.extraction.link_relevancy import should_enqueue_link
        from src.utils import fetch_provenance as fp

        wl = whitelist if whitelist is not None else None
        bl = (
            blacklist if blacklist is not None else _safe_load_blacklist(load_blacklist)
        )
        for la in extract_link_assets(content, base_url):
            summary["links"] += 1
            if la.is_anchor:
                summary["anchors"] += 1
                continue
            enqueue, verdict = should_enqueue_link(
                la.url,
                la.text,
                context=book,
                grok_client=grok_client,
                whitelist=wl,
                blacklist=bl,
            )
            if not enqueue:
                summary["dropped"] += 1
                logger.info(
                    "OBSERV link_asset.dropped verdict=%s url=%s", verdict, la.url
                )
                continue
            _enqueue_one(fp, la, book, base_url, verdict, summary)
    except Exception as e:  # noqa: BLE001 - enqueue must never crash the pipeline
        logger.warning("OBSERV link_asset.error reason=%s", str(e)[:120])
    logger.info(
        "OBSERV link_asset.summary book=%s links=%d enqueued=%d dropped=%d anchors=%d",
        book,
        summary["links"],
        summary["enqueued"],
        summary["dropped"],
        summary["anchors"],
    )
    return summary


def _safe_load_blacklist(loader) -> List[str]:
    """Load the license-rejection blacklist; [] on any error (fail-safe)."""
    try:
        return loader()
    except Exception:  # noqa: BLE001
        return []


def _enqueue_one(
    fp: Any, la: "LinkAsset", book: str, source_url: str, verdict: str, summary: dict
) -> None:
    """Record one review-eligible link as a fetch_provenance TEXTUAL HIT (-> PENDING). Carries full
    provenance so a reviewer can trace the link back to the exact source document it was cited in.
    Fail-safe."""
    try:
        fp.record_fetch_event(
            provider="link-asset",
            endpoint=la.url,
            kind=fp.TEXTUAL,
            outcome=fp.HIT,
            request_params={
                "media_type": la.media_type,
                "relevancy": verdict,
                "link_text": la.text[:200],
                "source_doc": book,
                "source_url": source_url,  # the parsed doc's _source_url -> trace to source material
            },
            result_summary=(
                f"{la.media_type} link ({verdict}) from {book} <{source_url}>: {la.text[:80]}"
            ),
        )
        summary["enqueued"] += 1
        logger.info(
            "OBSERV link_asset.enqueued media=%s verdict=%s source_url=%s url=%s",
            la.media_type,
            verdict,
            source_url,
            la.url,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("OBSERV link_asset.error url=%s reason=%s", la.url, str(e)[:120])
