"""Tests for the Phase 0 PDF -> Markdown routing seam."""

from pathlib import Path

import fitz
import pytest

from src.ingestion.pdf_pipeline import (
    STATUS_CONVERTED,
    STATUS_NEEDS_OCR,
    STATUS_UNSUPPORTED,
    convert_pdf_to_markdown,
)


def _digital_pdf(path: Path, pages: int = 2) -> Path:
    """Create a small born-digital PDF with a vector text layer."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text(
            (72, 72),
            f"Page {i + 1}. The 1st Infantry Division advanced at dawn. "
            "This is native vector text, not a scan.",
        )
    doc.save(str(path))
    doc.close()
    return path


def _scanned_like_pdf(path: Path, pages: int = 2) -> Path:
    """Create a PDF whose pages are full-page raster images (scanned-like).

    Each page is covered by a rendered image spanning the full page rect, which
    is what ``detect_scanned`` keys on (image_area_fraction ~1.0).
    """
    # Build a source image page, rasterize it, then place it full-page.
    src = fitz.open()
    src_page = src.new_page()
    src_page.insert_text((50, 50), "scanned page content")
    pix = src_page.get_pixmap(dpi=72)
    img_bytes = pix.tobytes("png")
    src.close()

    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(page.rect, stream=img_bytes)
    doc.save(str(path))
    doc.close()
    return path


def test_digital_pdf_converts_to_markdown(tmp_path: Path) -> None:
    pdf = _digital_pdf(tmp_path / "digital.pdf")
    out = tmp_path / "ocr_output" / "digital.md"
    outcome = convert_pdf_to_markdown(pdf, "digital", out)

    assert outcome.status == STATUS_CONVERTED
    assert outcome.converted is True
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "Infantry Division" in text
    # Manifest is always saved as the hand-off artifact.
    assert outcome.manifest_path is not None and outcome.manifest_path.exists()
    assert outcome.page_count == 2


def test_scanned_pdf_defers_to_chandra(tmp_path: Path) -> None:
    pdf = _scanned_like_pdf(tmp_path / "scanned.pdf")
    out = tmp_path / "ocr_output" / "scanned.md"
    outcome = convert_pdf_to_markdown(pdf, "scanned", out)

    assert outcome.status == STATUS_NEEDS_OCR
    assert outcome.converted is False
    # No in-process markdown written (would be low quality for scanned tables).
    assert not out.exists()
    # But the routing manifest IS saved for the Chandra bridge to consume.
    assert outcome.manifest_path is not None and outcome.manifest_path.exists()
    assert "chandra" in outcome.note.lower()


def test_unsupported_media_is_skipped(tmp_path: Path) -> None:
    # A non-PDF file with a .pdf name but no PDF magic bytes -> not a real PDF.
    fake = tmp_path / "not_really.pdf"
    fake.write_text("<html><body>hello</body></html>", encoding="utf-8")
    out = tmp_path / "ocr_output" / "not_really.md"
    outcome = convert_pdf_to_markdown(fake, "fake", out)

    assert outcome.status == STATUS_UNSUPPORTED
    assert not out.exists()


def test_manifest_saved_next_to_markdown(tmp_path: Path) -> None:
    pdf = _digital_pdf(tmp_path / "doc.pdf", pages=1)
    out = tmp_path / "ocr_output" / "doc.md"
    outcome = convert_pdf_to_markdown(pdf, "doc", out)
    assert outcome.manifest_path == tmp_path / "ocr_output" / "doc.manifest.json"


def test_missing_pdf_raises(tmp_path: Path) -> None:
    # build_source_metadata raises FileNotFoundError for a missing file.
    with pytest.raises(FileNotFoundError):
        convert_pdf_to_markdown(
            tmp_path / "nope.pdf", "nope", tmp_path / "ocr_output" / "nope.md"
        )
