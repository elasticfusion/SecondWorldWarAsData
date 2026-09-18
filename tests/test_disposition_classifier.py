"""Tests for the per-page disposition classifier.

Builds small synthetic PDFs with fitz so classification is exercised on real
PyMuPDF output rather than mocks.
"""

from pathlib import Path

import fitz
import pytest

from src.ingestion.disposition import DispositionResult
from src.ingestion.disposition_classifier import (
    REVIEW_BELOW,
    classify_page,
    classify_pdf,
    compute_page_signals,
    detect_scanned,
)


def _prose_page(doc: fitz.Document) -> fitz.Page:
    page = doc.new_page()
    prose = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 40
    page.insert_textbox(fitz.Rect(50, 50, 545, 780), prose, fontsize=11)
    return page


def _image_page(doc: fitz.Document) -> fitz.Page:
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 500, 700))
    pix.clear_with(128)
    page.insert_image(fitz.Rect(40, 40, 555, 760), pixmap=pix)
    return page


def _table_page(doc: fitz.Document) -> fitz.Page:
    page = doc.new_page()
    x0, y0, rows, cols, cw, ch = 50, 50, 8, 4, 120, 40
    shape = page.new_shape()
    for i in range(rows + 1):
        shape.draw_line(
            fitz.Point(x0, y0 + i * ch), fitz.Point(x0 + cols * cw, y0 + i * ch)
        )
    for j in range(cols + 1):
        shape.draw_line(
            fitz.Point(x0 + j * cw, y0), fitz.Point(x0 + j * cw, y0 + rows * ch)
        )
    shape.finish()
    shape.commit()
    for i in range(rows):
        for j in range(cols):
            page.insert_text(
                fitz.Point(x0 + j * cw + 5, y0 + i * ch + 25), f"r{i}c{j}", fontsize=9
            )
    return page


def _sparse_page(doc: fitz.Document) -> fitz.Page:
    page = doc.new_page()
    page.insert_text((72, 72), "Fig. 3")
    return page


# --- single-page classification -----------------------------------------


def test_prose_page_is_unstructured() -> None:
    doc = fitz.open()
    result = classify_page("S", _prose_page(doc), 1)
    assert result.disposition == "unstructured"
    assert result.confidence > REVIEW_BELOW
    assert result.needs_review is False
    assert result.signals.char_count > 1000


def test_image_dominant_page_is_image_and_flagged() -> None:
    doc = fitz.open()
    result = classify_page("S", _image_page(doc), 2)
    assert result.disposition == "image"
    # Image/map boundary is fuzzy -> flagged for review.
    assert result.needs_review is True
    assert result.signals.image_area_fraction > 0.5


def test_table_page_is_structured() -> None:
    doc = fitz.open()
    result = classify_page("S", _table_page(doc), 3)
    assert result.disposition == "structured"
    assert result.signals.table_count >= 1
    assert result.needs_review is False


def test_sparse_page_defaults_unstructured_and_flagged() -> None:
    doc = fitz.open()
    result = classify_page("S", _sparse_page(doc), 4)
    assert result.disposition == "unstructured"
    assert result.needs_review is True


def test_override_wins() -> None:
    doc = fitz.open()
    result = classify_page("S", _prose_page(doc), 1, override="map")
    assert result.disposition == "map"
    assert result.confidence == 1.0
    assert result.decision_source == "config_override"
    assert result.needs_review is False


# --- whole-PDF classification (mixed document) ---------------------------


def _write_mixed_pdf(tmp_path: Path) -> Path:
    doc = fitz.open()
    _prose_page(doc)  # page 1
    _table_page(doc)  # page 2
    _image_page(doc)  # page 3
    path = tmp_path / "mixed.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_classify_pdf_mixed_document(tmp_path: Path) -> None:
    path = _write_mixed_pdf(tmp_path)
    results = classify_pdf("S", path)
    assert len(results) == 3
    assert [r.page_number for r in results] == [1, 2, 3]
    assert results[0].disposition == "unstructured"
    assert results[1].disposition == "structured"
    assert results[2].disposition == "image"
    assert all(isinstance(r, DispositionResult) for r in results)


def test_classify_pdf_honors_overrides(tmp_path: Path) -> None:
    path = _write_mixed_pdf(tmp_path)
    results = classify_pdf("S", path, overrides={3: "map"})
    assert results[2].disposition == "map"
    assert results[2].decision_source == "config_override"
    # Non-overridden pages still classified heuristically.
    assert results[0].decision_source == "heuristic"


def test_result_serialization_round_trips_shape(tmp_path: Path) -> None:
    path = _write_mixed_pdf(tmp_path)
    result = classify_pdf("S", path)[0]
    data = result.to_dict()
    assert data["disposition"] == "unstructured"
    assert data["page_number"] == 1
    assert "signals" in data and "char_count" in data["signals"]


def test_compute_signals_fractions_bounded() -> None:
    doc = fitz.open()
    signals = compute_page_signals(_image_page(doc))
    assert 0.0 <= signals.image_area_fraction <= 1.0
    assert 0.0 <= signals.text_area_fraction <= 1.0


@pytest.mark.parametrize("bad_page", [0, -1])
def test_page_numbering_is_preserved(tmp_path: Path, bad_page: int) -> None:
    # Guard: classify_page records whatever page number it is given (the caller
    # in classify_pdf supplies 1-based numbers).
    doc = fitz.open()
    result = classify_page("S", _sparse_page(doc), bad_page)
    assert result.page_number == bad_page


# --- scanned-document regime (step 6 finding) ----------------------------


def _scanned_text_page(doc: fitz.Document) -> None:
    """A full-page scan image WITH an OCR-like text layer (tabular content).

    Simulates a scanned roster page: a raster covers the whole page and there
    is an OCR text layer. Geometry cannot tell this is a table.
    """
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 595, 842))
    pix.clear_with(240)
    page.insert_image(page.rect, pixmap=pix)
    # OCR-like text layer over the scan.
    page.insert_textbox(
        fitz.Rect(50, 50, 545, 780),
        "Comdg Gen 15 Sep 1943 Maj Gen William C Lee\n"
        "Asst Div Comdr 1 Aug 1944 Brig Gen Gerald J Higgins\n" * 8,
        fontsize=10,
    )


def _scanned_blank_page(doc: fitz.Document) -> None:
    """A full-page scan image with essentially no text layer."""
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 595, 842))
    pix.clear_with(240)
    page.insert_image(page.rect, pixmap=pix)


def _scanned_pdf(tmp_path: Path, blank_last: bool = True) -> Path:
    doc = fitz.open()
    for _ in range(6):
        _scanned_text_page(doc)
    if blank_last:
        _scanned_blank_page(doc)
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_detect_scanned_true_for_full_page_images(tmp_path: Path) -> None:
    path = _scanned_pdf(tmp_path)
    with fitz.open(str(path)) as doc:
        assert detect_scanned(doc) is True


def test_detect_scanned_false_for_native_prose(tmp_path: Path) -> None:
    doc = fitz.open()
    for _ in range(4):
        _prose_page(doc)
    path = tmp_path / "native.pdf"
    doc.save(str(path))
    doc.close()
    with fitz.open(str(path)) as native:
        assert detect_scanned(native) is False


def test_scanned_text_page_is_unstructured_not_structured(tmp_path: Path) -> None:
    # The core fix: a scanned table page must NOT be confidently mislabeled.
    # It routes to unstructured + review, deferring structure to the markdown.
    path = _scanned_pdf(tmp_path)
    results = classify_pdf("S", path)
    text_pages = [r for r in results if r.signals.char_count > 100]
    assert text_pages, "expected scanned text pages in fixture"
    for result in text_pages:
        assert result.disposition == "unstructured"
        assert result.signals.scanned is True
        assert result.needs_review is True
        assert "markdown" in result.notes.lower()


def test_scanned_blank_page_is_image(tmp_path: Path) -> None:
    path = _scanned_pdf(tmp_path, blank_last=True)
    results = classify_pdf("S", path)
    assert results[-1].disposition == "image"
    assert results[-1].signals.scanned is True


def test_native_path_unaffected_by_scanned_logic(tmp_path: Path) -> None:
    # A native prose PDF must still classify as before (not scanned).
    doc = fitz.open()
    _prose_page(doc)
    path = tmp_path / "native.pdf"
    doc.save(str(path))
    doc.close()
    result = classify_pdf("S", path)[0]
    assert result.signals.scanned is False
    assert result.disposition == "unstructured"
    assert result.needs_review is False  # clear native decision
    assert result.confidence > REVIEW_BELOW
