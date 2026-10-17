"""Source acquisition: download + stage positively-verified source documents so their text
flows through the pipeline, while preserving the original in cold storage.

This module is the DECISION + REGISTRY layer (no network I/O itself):

- :func:`acquisition_decision` — scales a human-review gate by FORMAT + SIZE. Image-based
  material (NARA page-image sets, PDFs) is expensive/uncertain, so it reviews at a LOW
  threshold; text formats (EPUB/DOC/CSV/TXT/HTML) auto-process at a MUCH higher threshold.
- A pluggable FETCHER REGISTRY keyed by ``search_source`` (nara_catalog / archive_org /
  openserp / future) so new sources register one fetcher without touching call sites.
- :func:`plan_acquisition` — combines a resolved bibliography entry + its registered fetcher
  into an acquisition plan (what to download, where the original lands, the review verdict).

Flow (realised by the acquisition pass that uses this module): ORIGINAL -> ``source/`` (the
existing SourcePdfsToGlacierIR lifecycle transitions it to Glacier IR at 30d) -> CONVERT to
text (Phase-0 front-end) under ``contentrepository/{SOURCE_ID}/{TITLE}/`` -> Phase 1/2/3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Review-gate thresholds (format-scaled) ------------------------------------------------
# Image-based material is expensive to OCR and uncertain, so it reviews at a LOW page
# threshold. Text formats are cheap/reliable, so they auto-process to a MUCH higher one.
_IMAGE_PAGE_REVIEW_THRESHOLD = (
    50  # NARA object-count / PDF page-count at/above -> review
)
_TEXT_PAGE_REVIEW_THRESHOLD = (
    2000  # text formats auto-process well beyond typical sizes
)

# Formats treated as IMAGE-BASED (frequently scanned -> low review threshold). PDFs included
# deliberately per design (many are image scans).
_IMAGE_FORMATS = frozenset({"pdf", "tif", "tiff", "jpg", "jpeg", "png", "gif", "image"})
# Text/structured formats -> high threshold (cheap, reliable conversion).
_TEXT_FORMATS = frozenset(
    {"epub", "doc", "docx", "txt", "text", "csv", "html", "htm", "md", "rtf", "xml"}
)


@dataclass(frozen=True)
class AcquisitionDecision:
    """Outcome of the review gate for one candidate document."""

    verdict: str  # "auto" | "review" | "skip"
    reason: str
    fmt: str
    pages: int


def _classify_format(fmt: str) -> str:
    """'image' | 'text' | 'unknown' for a (lowercased) format token/extension."""
    f = (fmt or "").lower().lstrip(".")
    if f in _IMAGE_FORMATS:
        return "image"
    if f in _TEXT_FORMATS:
        return "text"
    return "unknown"


def acquisition_decision(
    fmt: str,
    pages: int = 0,
    *,
    public_domain: bool,
) -> AcquisitionDecision:
    """Decide auto-process / human-review / skip for a positively-verified document.

    - NON public-domain -> always REVIEW (licensing gate), regardless of size/format.
    - IMAGE-based (PDF, NARA page sets, scans) -> REVIEW at/above the low image threshold
      (>= 50 pages/objects); below it, auto.
    - TEXT formats -> auto up to a much higher threshold.
    - UNKNOWN format -> REVIEW (conservative).
    """
    fmt_class = _classify_format(fmt)
    n = max(int(pages or 0), 0)
    if not public_domain:
        return AcquisitionDecision("review", "non_public_domain_license", fmt_class, n)
    if fmt_class == "image":
        if n >= _IMAGE_PAGE_REVIEW_THRESHOLD:
            return AcquisitionDecision(
                "review", f"image_pages>={_IMAGE_PAGE_REVIEW_THRESHOLD}", fmt_class, n
            )
        return AcquisitionDecision("auto", "image_small", fmt_class, n)
    if fmt_class == "text":
        if n >= _TEXT_PAGE_REVIEW_THRESHOLD:
            return AcquisitionDecision(
                "review", f"text_pages>={_TEXT_PAGE_REVIEW_THRESHOLD}", fmt_class, n
            )
        return AcquisitionDecision("auto", "text", fmt_class, n)
    return AcquisitionDecision("review", "unknown_format", fmt_class, n)


# --- Pluggable fetcher registry ------------------------------------------------------------
# A fetcher inspects a resolved bibliography entry and returns an AcquisitionTarget describing
# what to download (or None if it can't). Registered by search_source so new sources plug in.


@dataclass(frozen=True)
class AcquisitionTarget:
    """What a fetcher determined should be downloaded for an entry."""

    fmt: str  # artifact format token (e.g. "pdf", "tif", "epub")
    pages: int  # page/object count (drives the review gate); 0 if unknown
    object_urls: List[str] = field(
        default_factory=list
    )  # direct bytes URLs (page scans / file)
    detail: str = ""  # human-readable note (e.g. "NARA naId 685290042, 1294 objects")


# source_source -> fetcher(entry: dict, config: dict) -> Optional[AcquisitionTarget]
_FETCHERS: Dict[
    str, Callable[[Dict[str, Any], Dict[str, Any]], Optional["AcquisitionTarget"]]
] = {}


def register_fetcher(
    search_source: str,
    fetcher: Callable[[Dict[str, Any], Dict[str, Any]], Optional["AcquisitionTarget"]],
) -> None:
    """Register a fetcher for a ``search_source`` (idempotent — last registration wins)."""
    _FETCHERS[search_source] = fetcher


def get_fetcher(
    search_source: str,
) -> Optional[
    Callable[[Dict[str, Any], Dict[str, Any]], Optional["AcquisitionTarget"]]
]:
    """The registered fetcher for a source, or None if unsupported."""
    return _FETCHERS.get(search_source)


def registered_sources() -> List[str]:
    """Sorted list of sources that have a registered fetcher (for observability)."""
    return sorted(_FETCHERS)


@dataclass(frozen=True)
class AcquisitionPlan:
    """A fully-resolved plan for one entry: what to fetch + the review verdict + where the
    original lands. Consumed by the acquisition pass (which performs the actual I/O)."""

    bib_id: str
    search_source: str
    target: "AcquisitionTarget"
    decision: AcquisitionDecision
    source_prefix: str  # S3 key prefix for the preserved original (under source/)


def _is_public_domain(entry: Dict[str, Any]) -> bool:
    cp = entry.get("copyright_status") or {}
    return (
        str(cp.get("status") or entry.get("license") or "").lower() == "public_domain"
    )


def plan_acquisition(
    entry: Dict[str, Any], config: Optional[Dict[str, Any]] = None
) -> Optional[AcquisitionPlan]:
    """Build an acquisition plan for a RESOLVED bibliography entry, or None if it is not
    acquirable (unresolved, no registered fetcher, or the fetcher finds nothing).

    Pure decision logic — performs no I/O. The caller (acquisition pass) runs the fetch,
    honouring ``decision.verdict`` (auto vs review) and the licensing gate already applied.
    """
    cfg = config or {}
    if entry.get("search_status") != "resolved":
        return None
    source = str(entry.get("search_source") or "")
    fetcher = get_fetcher(source)
    if fetcher is None:
        logger.debug("No acquisition fetcher registered for source=%s", source)
        return None
    try:
        target = fetcher(entry, cfg)
    except Exception as e:  # noqa: BLE001 - a fetcher must never crash the pass
        logger.warning("acquisition fetcher %s failed: %s", source, e)
        return None
    if target is None:
        return None
    decision = acquisition_decision(
        target.fmt, target.pages, public_domain=_is_public_domain(entry)
    )
    bib_id = str(entry.get("BibliographyID") or "unknown")
    # Original preserved under source/<search_source>/<bib_id>/ — covered by the existing
    # SourcePdfsToGlacierIR lifecycle (Glacier IR @30d).
    source_prefix = f"source/{source}/{bib_id}/"
    return AcquisitionPlan(bib_id, source, target, decision, source_prefix)
