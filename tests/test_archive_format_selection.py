"""Tests for deliberate Archive.org format selection.

PDF: prefer source=original over derivatives, then largest (fullest scan).
Text: record ALL processing formats (nothing discarded). Prefer EPUB as the
default (pandoc converts it WITH embedded images — photos/maps the raw DjVuTXT
lacks); flag a large EPUB (usually = embedded images we want) rather than reject
it; DjVuTXT recorded as the lightweight text-only alternative.
"""

from src.extraction.supplemental_search import (
    _select_archive_pdf,
    _archive_text_formats,
    _LARGE_EPUB_BYTES,
)

IDENT = "x"


def test_pdf_prefers_original_over_derivative():
    files = [
        {"name": "x_bw.pdf", "source": "derivative", "size": "9000000"},
        {"name": "x.pdf", "source": "original", "size": "5000000"},
    ]
    assert _select_archive_pdf(IDENT, files).endswith("/x.pdf")


def test_pdf_largest_when_same_source():
    files = [
        {"name": "x_a.pdf", "source": "derivative", "size": "1000"},
        {"name": "x_b.pdf", "source": "derivative", "size": "9000"},
    ]
    assert _select_archive_pdf(IDENT, files).endswith("/x_b.pdf")


def test_no_pdf_returns_none():
    assert _select_archive_pdf(IDENT, [{"name": "x_djvu.txt"}]) is None


def test_epub_and_txt_both_recorded():
    files = [
        {"name": "x.epub", "format": "EPUB", "size": "2000000"},
        {"name": "x_djvu.txt", "format": "DjVuTXT", "size": "3000000"},
    ]
    out = _archive_text_formats(IDENT, files)
    # Nothing discarded — both formats available.
    assert out["epub_url"].endswith("/x.epub")
    assert out["djvu_txt_url"].endswith("/x_djvu.txt")
    # Default prefers EPUB (structure + embedded images).
    assert out["text_format"] == "epub"
    assert out["text_url"] == out["epub_url"]


def test_large_epub_flagged_not_rejected():
    files = [
        {"name": "x.epub", "format": "EPUB", "size": str(_LARGE_EPUB_BYTES + 1)},
        {"name": "x_djvu.txt", "format": "DjVuTXT", "size": "3000000"},
    ]
    out = _archive_text_formats(IDENT, files)
    # The large EPUB is STILL recorded and STILL the default (images we want),
    # just flagged as large — not dropped in favor of txt.
    assert out["epub_url"].endswith("/x.epub")
    assert out["text_format"] == "epub_large"
    assert out["text_url"] == out["epub_url"]
    assert out["djvu_txt_url"].endswith("/x_djvu.txt")  # alt still recorded


def test_txt_only_defaults_to_txt():
    files = [{"name": "x_djvu.txt", "format": "DjVuTXT", "size": "3000000"}]
    out = _archive_text_formats(IDENT, files)
    assert out["text_format"] == "djvu_txt"
    assert "epub_url" not in out


def test_no_text_formats_empty():
    assert _archive_text_formats(IDENT, [{"name": "x.pdf"}]) == {}


def test_richest_epub_chosen_when_multiple():
    files = [
        {"name": "x_small.epub", "format": "EPUB", "size": "1000"},
        {"name": "x_rich.epub", "format": "EPUB", "size": "9000000"},
    ]
    out = _archive_text_formats(IDENT, files)
    assert out["epub_url"].endswith("/x_rich.epub")  # richest = most images
