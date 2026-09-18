"""Disposition model for the ingestion front-end.

A *disposition* is how a single page/section of a source document should be
converted: as prose (``unstructured``), tabular data (``structured``), an
``image``, or a ``map``. A single source is frequently mixed, so disposition is
classified per page/section rather than per document.

This module defines the result types; the classifier lives in
``disposition_classifier``. See
docs/current/dataquality/INGESTION_FRONT_END.md (step 3).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Literal, Optional

Disposition = Literal["structured", "unstructured", "image", "map"]

# How a disposition was decided, for auditability.
DecisionSource = Literal["heuristic", "config_override"]


@dataclass
class PageSignals:
    """Raw measurements a classifier derived from one page.

    Retained on the result so a disposition can be explained and tuned without
    re-parsing the source.

    Attributes:
        char_count: Number of extracted text characters on the page.
        text_area_fraction: Fraction of page area covered by text blocks.
        image_area_fraction: Fraction of page area covered by image blocks.
        drawing_count: Number of vector-drawing paths (a map cue).
        table_count: Number of tables detected on the page.
        table_area_fraction: Fraction of page area covered by detected tables.
        scanned: True when the page is a full-page scan image (a raster covers
            the whole page), so geometry-based structure signals do not apply.
    """

    char_count: int = 0
    text_area_fraction: float = 0.0
    image_area_fraction: float = 0.0
    drawing_count: int = 0
    table_count: int = 0
    table_area_fraction: float = 0.0
    scanned: bool = False


@dataclass
class DispositionResult:  # pylint: disable=too-many-instance-attributes
    """Classification of one page/section of a source document.

    Attributes:
        source_id: Identifier of the owning source (see SourceMetadata).
        page_number: 1-based page number for PDFs (None for non-paged sources).
        section_id: Optional section identifier for non-paged sources.
        disposition: The chosen disposition.
        confidence: Classifier confidence in [0.0, 1.0].
        needs_review: True when the decision is ambiguous and a human should
            confirm it (e.g. low confidence, or a fuzzy image/map boundary).
        decision_source: Whether the disposition came from a heuristic or a
            config override.
        provenance_anchor: Citation anchor back to the original (e.g. a page
            number string or section offset).
        signals: The raw measurements behind the decision.
        notes: Free text (e.g. why review was flagged, or the override key).
    """

    source_id: str
    disposition: Disposition
    confidence: float
    page_number: Optional[int] = None
    section_id: Optional[str] = None
    needs_review: bool = False
    decision_source: DecisionSource = "heuristic"
    provenance_anchor: str = ""
    signals: PageSignals = field(default_factory=PageSignals)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)
