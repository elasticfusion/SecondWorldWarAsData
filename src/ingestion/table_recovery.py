"""Auto-routing bridge: flattened-table detection -> PP-StructureV3 recovery.

This wires two existing pieces into an automatic pipeline:

1. :func:`markdown_structure.detect_flattened_tables` recognizes the flattened
   2-D task-organization tables Chandra produces (its structural blind spot #2),
   returning ``needs_review`` hint trees — but it never reconstructs the grid.
2. :class:`paddle_structure.PaddleStructureRunner` recovers a real ``<table>``
   from the page image (validated PoC).

The routing decision is: **if Chandra's markdown for a page contains a flattened
task-org table, render that page and run PP-StructureV3 to recover the grid.**
Pages Chandra handled correctly (already-``<table>`` output, prose, images) are
NOT sent to Paddle — recovery only fires where it is needed, so the extra
(GPU) cost is spent only on the pages that flattened. This is the pre-emptive
routing discussed in CHANDRA_OCR_DESIGN.md ("route table-heavy / suspect pages
through PP-StructureV3").

The bridge is deliberately conservative and non-destructive:

* It returns *both* Chandra's original markdown (with its review-flagged hint
  tree) and Paddle's recovered table — it does not silently replace one with the
  other. Reconciling the two (Chandra's stronger character fidelity vs. Paddle's
  structure) is a downstream/human step, matching the "ensemble as verification"
  stance.
* When PaddleOCR is unavailable, or the page cannot be rendered, or recovery
  yields no table, it degrades to "detected, not recovered" and the pipeline
  keeps Chandra's flagged output.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.ingestion.markdown_structure import (
    FlattenedTableSpan,
    detect_flattened_tables,
)
from src.ingestion.paddle_structure import (
    PaddleStructureRunner,
    RecoveredTable,
    is_available,
)

logger = logging.getLogger(__name__)

# Render DPI for the page image handed to PP-StructureV3. Matches the OCR
# pipeline's standard (submit_ocr_job.py default; see CHANDRA_OCR_DESIGN.md
# "Render DPI" — 300 is the sweet spot above which scale_to_fit caps gains).
RECOVERY_RENDER_DPI = 300


@dataclass
class PageTableRecovery:
    """Outcome of routing one page through flattened-table detection + recovery.

    Attributes:
        routed: True if the page contained a flattened table and was sent to
            PP-StructureV3 (whether or not recovery ultimately produced a table).
        flattened_spans: The detector's review-flagged hint trees (Chandra side).
        recovered: The PP-StructureV3 result, or None if not routed / no runner.
        notes: Free text describing the routing decision.
    """

    routed: bool = False
    flattened_spans: List[FlattenedTableSpan] = field(default_factory=list)
    recovered: Optional[RecoveredTable] = None
    notes: str = ""

    @property
    def recovered_a_table(self) -> bool:
        """True when routing produced at least one recovered ``<table>``."""
        return bool(self.recovered and self.recovered.table_count > 0)


def page_needs_recovery(markdown: str) -> List[FlattenedTableSpan]:
    """Return flattened task-org spans in a page's Chandra markdown (may be []).

    A non-empty result is the routing trigger: this page flattened a 2-D table
    and is a candidate for PP-StructureV3 recovery.
    """
    return detect_flattened_tables(markdown).spans


def _render_pdf_page_to_png(pdf_path: Path, page_number: int, dpi: int) -> Path:
    """Render a 1-based PDF page to a temporary PNG and return its path."""
    import fitz  # local import: only needed when actually rendering

    zoom = dpi / 72.0  # PDF user space is 72 dpi
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        tmp = Path(
            tempfile.mkstemp(prefix=f"recover_p{page_number}_", suffix=".png")[1]
        )
        pix.save(str(tmp))
    return tmp


def recover_page_tables(
    markdown: str,
    *,
    page_image: Optional[Path] = None,
    pdf_path: Optional[Path] = None,
    page_number: Optional[int] = None,
    runner: Optional[PaddleStructureRunner] = None,
    dpi: int = RECOVERY_RENDER_DPI,
) -> PageTableRecovery:
    """Route one page: detect flattened tables and, if any, recover the grid.

    Provide the page image directly (``page_image``) or a ``pdf_path`` +
    ``page_number`` to render on demand. If neither is given, the page is
    detected-but-not-recovered (useful for the routing decision alone).

    Args:
        markdown: Chandra's markdown for this single page.
        page_image: Pre-rendered page image to hand to PP-StructureV3.
        pdf_path: Source PDF (rendered on demand if ``page_image`` is absent).
        page_number: 1-based page number to render from ``pdf_path``.
        runner: An existing :class:`PaddleStructureRunner` (reused across pages
            to avoid rebuilding the model); one is created if omitted.
        dpi: Render DPI when rendering from a PDF.

    Returns:
        A :class:`PageTableRecovery`. Never raises for the expected failure
        modes (no Paddle, no image, no recovered table) — it degrades to
        "detected, not recovered" so the caller keeps Chandra's flagged output.
    """
    spans = page_needs_recovery(markdown)
    if not spans:
        return PageTableRecovery(routed=False, notes="no flattened table detected")

    if not is_available():
        return PageTableRecovery(
            routed=False,
            flattened_spans=spans,
            notes="flattened table detected; PaddleOCR unavailable, kept Chandra output",
        )

    # Resolve a page image (given, or rendered from the PDF).
    rendered_tmp: Optional[Path] = None
    image = page_image
    if image is None and pdf_path is not None and page_number is not None:
        try:
            image = rendered_tmp = _render_pdf_page_to_png(pdf_path, page_number, dpi)
        except Exception as exc:  # pragma: no cover - render failure
            return PageTableRecovery(
                routed=False,
                flattened_spans=spans,
                notes=f"flattened table detected; page render failed: {exc}",
            )
    if image is None:
        return PageTableRecovery(
            routed=False,
            flattened_spans=spans,
            notes="flattened table detected; no page image provided for recovery",
        )

    try:
        active_runner = runner or PaddleStructureRunner()
        recovered = active_runner.recover(image)
    finally:
        if rendered_tmp is not None:
            rendered_tmp.unlink(missing_ok=True)

    return PageTableRecovery(
        routed=True,
        flattened_spans=spans,
        recovered=recovered,
        notes=(
            f"routed to PP-StructureV3 ({recovered.device}); "
            f"{recovered.table_count} table(s) recovered"
        ),
    )
