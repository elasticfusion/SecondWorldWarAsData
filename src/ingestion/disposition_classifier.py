"""Per-page disposition classifier (heuristic + config override).

Classifies each page of a PDF as ``structured`` | ``unstructured`` | ``image``
| ``map`` using cheap, deterministic signals from PyMuPDF (``fitz``).

Two regimes:

* **Native PDFs** (vector text, embedded raster images): geometry signals work
  well — text density -> unstructured; detected tables -> structured;
  image-area dominance (+ drawings) -> image/map.
* **Scanned PDFs** (every page is a full-page scan image, usually with an OCR
  text layer): geometry cannot distinguish structure. Validated on the ETO
  Order of Battle: ``image_area_fraction`` is ~1.0 on every page, ``find_tables``
  finds nothing (no vector layer), and OCR span geometry does not separate
  tables from prose. So for scanned pages this classifier does NOT guess
  structure. Text-bearing scanned pages are marked ``unstructured`` and flagged
  for review, with a note that STRUCTURE for scanned documents is recovered from
  the OCR+AI (Chandra) markdown, not from PDF geometry (see
  INGESTION_FRONT_END.md, "scanned documents"). Near-empty-text scanned pages
  are treated as ``image``.

Ambiguous pages get a low confidence and ``needs_review=True``. A config
override always wins and is recorded as such. No ML is used.

See docs/current/dataquality/INGESTION_FRONT_END.md (step 3, and the scanned-
document finding under step 6).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import fitz

from src.ingestion.disposition import (
    Disposition,
    DispositionResult,
    PageSignals,
)

logger = logging.getLogger(__name__)

# --- Tunable thresholds --------------------------------------------------
# Kept as module constants so they can be adjusted without touching logic.

# A page with at least this many characters is text-bearing enough to be prose
# (absent a stronger structured/image signal).
PROSE_MIN_CHARS = 200

# Tables covering at least this fraction of the page make it structured.
STRUCTURED_TABLE_AREA = 0.15

# Images covering at least this fraction of the page make it image/map.
IMAGE_AREA_DOMINANT = 0.55

# Below this text-area fraction, a large image is considered to have only
# incidental text (captions/legends) rather than being a prose page.
SPARSE_TEXT_AREA = 0.15

# A page with at least this many vector-drawing paths is map-like (maps are
# drawing-heavy: borders, roads, symbols).
MAP_DRAWING_COUNT = 40

# Confidence assigned to a clear, unambiguous decision.
CONF_CLEAR = 0.9
# Confidence for a decision made on weaker/mixed signals.
CONF_WEAK = 0.55
# At/below this confidence a result is flagged for human review.
REVIEW_BELOW = 0.6

# --- Scanned-document detection ------------------------------------------
# A page is "scan-like" when a raster image covers essentially the whole page.
SCAN_IMAGE_AREA = 0.95
# A document is treated as scanned when at least this fraction of sampled pages
# are scan-like. Scanned docs cannot be structure-classified by geometry.
SCANNED_DOC_FRACTION = 0.8
# Number of pages sampled (evenly spaced) for the document-level scan check.
SCAN_SAMPLE_PAGES = 12
# On a scanned page, at least this many characters of OCR text means the page
# carries content (tables or prose) rather than being a bare image.
SCAN_TEXT_MIN_CHARS = 100


def _rect_area(bbox: tuple[float, float, float, float]) -> float:
    """Area of a PyMuPDF bbox tuple (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _measure_tables(page: "fitz.Page") -> tuple[int, float]:
    """Return (table_count, total_table_area) for a page.

    Isolated so signal collection stays flat and the table library's varied
    failure modes are handled in one place.
    """
    try:
        tables = page.find_tables()
    except (RuntimeError, ValueError, AttributeError) as exc:  # pragma: no cover
        logger.debug("find_tables failed: %s", exc)
        return 0, 0.0
    table_list = list(getattr(tables, "tables", []) or [])
    area = sum(_rect_area(tuple(tbl.bbox)) for tbl in table_list)
    return len(table_list), area


def _count_drawings(page: "fitz.Page") -> int:
    """Return the number of vector-drawing paths on a page (0 on failure)."""
    try:
        return len(page.get_drawings())
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive
        logger.debug("get_drawings failed: %s", exc)
        return 0


def compute_page_signals(page: "fitz.Page") -> PageSignals:
    """Measure the heuristic signals for a single PDF page."""
    page_area = _rect_area(tuple(page.rect)) or 1.0

    char_count = len((page.get_text() or "").strip())

    text_area = 0.0
    image_area = 0.0
    for block in page.get_text("dict").get("blocks", []):
        area = _rect_area(tuple(block.get("bbox", (0, 0, 0, 0))))
        if block.get("type") == 0:
            text_area += area
        elif block.get("type") == 1:
            image_area += area

    table_count, table_area = _measure_tables(page)

    return PageSignals(
        char_count=char_count,
        text_area_fraction=min(1.0, text_area / page_area),
        image_area_fraction=min(1.0, image_area / page_area),
        drawing_count=_count_drawings(page),
        table_count=table_count,
        table_area_fraction=min(1.0, table_area / page_area),
    )


def _page_is_scan_like(page: "fitz.Page") -> bool:
    """True when a raster image covers essentially the whole page."""
    signals = compute_page_signals(page)
    return signals.image_area_fraction >= SCAN_IMAGE_AREA


def detect_scanned(doc: "fitz.Document") -> bool:
    """Return True if the document is a scanned PDF (full-page images).

    Samples up to ``SCAN_SAMPLE_PAGES`` evenly-spaced pages and checks whether a
    dominant fraction are full-page raster scans. Cheap and document-level: the
    result drives whether per-page classification trusts geometry-based
    structure signals.
    """
    total = doc.page_count
    if total == 0:
        return False
    step = max(1, total // SCAN_SAMPLE_PAGES)
    sampled = list(range(0, total, step))
    scan_like = sum(1 for i in sampled if _page_is_scan_like(doc[i]))
    return scan_like / len(sampled) >= SCANNED_DOC_FRACTION


def _classify_scanned_page(signals: PageSignals) -> tuple[Disposition, float, str]:
    """Classify a page of a SCANNED document.

    Geometry cannot tell structured from unstructured here (see module docstring
    and INGESTION_FRONT_END.md). So: a page with an OCR text layer is routed as
    ``unstructured`` and flagged for review, with a note that structure is
    recovered from the OCR+AI markdown, not PDF geometry. A page with little or
    no text is treated as an ``image``.
    """
    if signals.char_count >= SCAN_TEXT_MIN_CHARS:
        return (
            "unstructured",
            CONF_WEAK,
            "scanned page with OCR text; geometry cannot classify structure "
            "(structure recovered from OCR+AI markdown, not PDF geometry)",
        )
    return "image", CONF_WEAK, "scanned page with little/no text layer"


def _classify_from_signals(signals: PageSignals) -> tuple[Disposition, float, str]:
    """Return (disposition, confidence, note) from measured signals.

    Order matters: image/map dominance is checked before structured/prose so a
    full-page scan is not mistaken for text, and tables are checked before
    prose so rosters are not mistaken for narrative.
    """
    # Image or map: a large image dominates the page with little real text.
    if (
        signals.image_area_fraction >= IMAGE_AREA_DOMINANT
        and signals.text_area_fraction < SPARSE_TEXT_AREA
    ):
        if signals.drawing_count >= MAP_DRAWING_COUNT:
            return "map", CONF_CLEAR, "large graphic with many vector paths"
        # Image vs. map is the fuzzy boundary: default to image, flag review.
        return "image", CONF_WEAK, "large image; image/map boundary uncertain"

    # Structured: detected tables cover a meaningful share of the page.
    if signals.table_count > 0 and signals.table_area_fraction >= STRUCTURED_TABLE_AREA:
        return "structured", CONF_CLEAR, "tables detected covering significant area"

    # Unstructured prose: enough text and no dominant table/image.
    if signals.char_count >= PROSE_MIN_CHARS:
        return "unstructured", CONF_CLEAR, "text-dense page"

    # Map-heavy vector page with little text but no image block (e.g. a vector
    # map drawn as paths rather than a raster image).
    if (
        signals.drawing_count >= MAP_DRAWING_COUNT
        and signals.char_count < PROSE_MIN_CHARS
    ):
        return "map", CONF_WEAK, "sparse text with many vector paths"

    # Sparse page: little of anything. Default to unstructured but review.
    return "unstructured", CONF_WEAK, "sparse page; weak signals"


def classify_page(
    source_id: str,
    page: "fitz.Page",
    page_number: int,
    *,
    override: Optional[Disposition] = None,
    scanned: bool = False,
) -> DispositionResult:
    """Classify a single page, honoring a config override when supplied.

    When ``scanned`` is True the page is classified with the scanned-document
    rules (geometry cannot determine structure); otherwise native-PDF geometry
    signals are used.
    """
    signals = compute_page_signals(page)
    signals.scanned = scanned

    if override is not None:
        return DispositionResult(
            source_id=source_id,
            disposition=override,
            confidence=1.0,
            page_number=page_number,
            needs_review=False,
            decision_source="config_override",
            provenance_anchor=str(page_number),
            signals=signals,
            notes="disposition forced by config override",
        )

    if scanned:
        disposition, confidence, note = _classify_scanned_page(signals)
    else:
        disposition, confidence, note = _classify_from_signals(signals)
    return DispositionResult(
        source_id=source_id,
        disposition=disposition,
        confidence=confidence,
        page_number=page_number,
        needs_review=confidence <= REVIEW_BELOW,
        decision_source="heuristic",
        provenance_anchor=str(page_number),
        signals=signals,
        notes=note,
    )


def classify_pdf(
    source_id: str,
    pdf_path: Path,
    *,
    overrides: Optional[Mapping[int, Disposition]] = None,
) -> List[DispositionResult]:
    """Classify every page of a PDF.

    Detects whether the document is scanned once, then classifies each page
    under the appropriate regime.

    Args:
        source_id: Identifier of the owning source.
        pdf_path: Path to the PDF.
        overrides: Optional map of 1-based page number -> forced disposition.

    Returns:
        One :class:`DispositionResult` per page, in page order.
    """
    override_map: Dict[int, Disposition] = dict(overrides or {})
    results: List[DispositionResult] = []
    with fitz.open(str(pdf_path)) as doc:
        scanned = detect_scanned(doc)
        for index, page in enumerate(doc):
            page_number = index + 1
            results.append(
                classify_page(
                    source_id,
                    page,
                    page_number,
                    override=override_map.get(page_number),
                    scanned=scanned,
                )
            )
    review = sum(1 for r in results if r.needs_review)
    logger.info(
        "Classified %d page(s) of %s (scanned=%s, %d flagged for review)",
        len(results),
        pdf_path.name,
        scanned,
        review,
    )
    return results
