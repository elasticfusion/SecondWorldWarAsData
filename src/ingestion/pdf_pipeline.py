"""Phase 0 PDF -> Markdown routing seam.

Ties the built ingestion front-end (``media_detection`` -> ``classify_pdf`` ->
``build_manifest`` -> ``convert_manifest``) into one entry point the Phase 0
orchestrator can call per source PDF, and — critically — **routes by
disposition so it never emits low-quality markdown for scanned tables**:

* **digital-text PDF** (native vector text): converted in-process with
  ``convert_manifest`` (PyMuPDF / ``pymupdf4llm``). Good quality; produces the
  markdown the downstream parsers consume.
* **scanned PDF** (every page a full-page raster, e.g. the ETO Order of Battle):
  the classifier marks pages ``unstructured``/``image`` and defers table
  structure to Chandra OCR+AI markdown. In-process ``pymupdf4llm`` would only
  scrape the raw OCR text layer — NOT Chandra's clean table HTML — so we do NOT
  convert here. The outcome is ``needs_ocr``: the routing manifest is saved and
  the PDF is handed off to the Chandra bridge (external AWS Batch GPU job;
  see docs/current/dataquality/CHANDRA_OCR_DESIGN.md). Wiring that bridge behind
  this same seam is the documented follow-up.
* **unsupported media**: recorded and skipped.

This keeps the quality contract honest: Phase 0 gains an end-to-end path for
digital PDFs today, and a clean, explicit hand-off point for scanned PDFs, with
no fabricated/garbled markdown entering the pipeline.

See docs/current/dataquality/INGESTION_FRONT_END.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz

from src.ingestion.disposition_classifier import classify_pdf, detect_scanned
from src.ingestion.media_detection import build_source_metadata
from src.ingestion.region_converter import convert_manifest
from src.ingestion.routing_manifest import build_manifest

logger = logging.getLogger(__name__)

# Outcome status values.
STATUS_CONVERTED = "converted"  # digital PDF -> markdown written in-process
STATUS_NEEDS_OCR = "needs_ocr"  # scanned PDF -> deferred to Chandra
STATUS_UNSUPPORTED = "unsupported"  # not a supported media type
STATUS_ERROR = "error"  # conversion failed


@dataclass
class PdfConversionOutcome:
    """Result of routing/converting one source PDF."""

    source_id: str
    pdf_path: Path
    status: str
    markdown_path: Optional[Path] = None
    manifest_path: Optional[Path] = None
    page_count: int = 0
    asset_count: int = 0
    note: str = ""

    @property
    def converted(self) -> bool:
        """True if markdown was produced in-process."""
        return self.status == STATUS_CONVERTED


def _is_scanned_pdf(pdf_path: Path) -> bool:
    """Return True if the PDF is a scanned document (full-page rasters)."""
    import fitz  # local import: heavy dep, only needed on the PDF path

    with fitz.open(str(pdf_path)) as doc:
        return detect_scanned(doc)


def convert_pdf_to_markdown(
    pdf_path: Path,
    source_id: str,
    markdown_out: Path,
    *,
    manifest_dir: Optional[Path] = None,
    assets_dir: Optional[Path] = None,
) -> PdfConversionOutcome:
    """Route one PDF and, if digital-text, convert it to Markdown on disk.

    Args:
        pdf_path: Source PDF.
        source_id: Stable identifier for the source (used in metadata/manifest).
        markdown_out: Path to write the converted Markdown to (digital PDFs
            only). Its parent is created if needed.
        manifest_dir: Directory to save the routing manifest JSON in. Defaults
            to ``markdown_out.parent``. The manifest is always saved (it is the
            hand-off artifact for the Chandra bridge on scanned PDFs).
        assets_dir: Directory for extracted image/map assets. Defaults to
            ``markdown_out.parent``.

    Returns:
        A :class:`PdfConversionOutcome`. Only ``STATUS_CONVERTED`` writes
        markdown; ``STATUS_NEEDS_OCR`` saves a manifest and defers to Chandra.
    """
    manifest_dir = manifest_dir or markdown_out.parent
    assets_dir = assets_dir or markdown_out.parent

    meta = build_source_metadata(source_id, pdf_path)
    if not meta.supported or meta.media_type != "pdf":
        logger.info(
            "Skipping %s: unsupported for PDF conversion (media_type=%s)",
            pdf_path.name,
            meta.media_type,
        )
        return PdfConversionOutcome(
            source_id=source_id,
            pdf_path=pdf_path,
            status=STATUS_UNSUPPORTED,
            note=f"media_type={meta.media_type}",
        )

    try:
        scanned = _is_scanned_pdf(pdf_path)
        results = classify_pdf(source_id, pdf_path)
        manifest = build_manifest(meta, results)
        manifest_path = manifest_dir / f"{pdf_path.stem}.manifest.json"
        manifest.save(manifest_path)
        page_count = manifest.summary.total_pages

        if scanned:
            logger.info(
                "%s is a scanned PDF (%d pages): deferring to Chandra OCR "
                "(manifest saved to %s)",
                pdf_path.name,
                page_count,
                manifest_path,
            )
            return PdfConversionOutcome(
                source_id=source_id,
                pdf_path=pdf_path,
                status=STATUS_NEEDS_OCR,
                manifest_path=manifest_path,
                page_count=page_count,
                note="scanned document — structure recovered via Chandra markdown",
            )

        result = convert_manifest(manifest, pdf_path, assets_dir)
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(result.markdown, encoding="utf-8")
        logger.info(
            "Converted digital PDF %s -> %s (%d page(s), %d asset(s))",
            pdf_path.name,
            markdown_out,
            page_count,
            len(result.assets),
        )
        return PdfConversionOutcome(
            source_id=source_id,
            pdf_path=pdf_path,
            status=STATUS_CONVERTED,
            markdown_path=markdown_out,
            manifest_path=manifest_path,
            page_count=page_count,
            asset_count=len(result.assets),
        )
    except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
        # Defensive boundary: one bad PDF must not abort the Phase 0 batch.
        logger.error("PDF conversion failed for %s: %s", pdf_path.name, exc)
        return PdfConversionOutcome(
            source_id=source_id,
            pdf_path=pdf_path,
            status=STATUS_ERROR,
            note=str(exc),
        )
