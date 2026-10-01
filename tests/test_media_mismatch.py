"""Tests for media-type MISMATCH detection (detect_media_mismatch).

A file whose declared extension contradicts its actual magic bytes must be
flagged so intake rejects it (never fed downstream). Only a CONFIDENT
contradiction flags — an unsniffable file is not a mismatch (can't prove wrong).
"""

from pathlib import Path

from src.ingestion.media_detection import detect_media_mismatch

# Magic-byte prefixes recognized by _sniff_magic_bytes.
_PDF = b"%PDF-1.7\n..."
_PNG = b"\x89PNG\r\n\x1a\n...."
_HTML = b"<!DOCTYPE html>\n<html>..."


def _write(tmp_path, name, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_epub_that_is_really_html_is_mismatch(tmp_path):
    # .epub extension but the bytes are HTML -> reject
    p = _write(tmp_path, "book.epub", _HTML)
    v = detect_media_mismatch(p)
    assert v.is_mismatch is True
    assert v.declared == "epub" and v.detected == "html"
    assert "epub" in v.reason and "html" in v.reason


def test_pdf_that_is_really_html_is_mismatch(tmp_path):
    p = _write(tmp_path, "doc.pdf", _HTML)
    v = detect_media_mismatch(p)
    assert v.is_mismatch is True
    assert v.declared == "pdf" and v.detected == "html"


def test_png_mislabeled_as_pdf_is_mismatch(tmp_path):
    p = _write(tmp_path, "scan.pdf", _PNG)
    v = detect_media_mismatch(p)
    assert v.is_mismatch is True
    assert v.declared == "pdf" and v.detected == "image"


def test_matching_extension_and_bytes_is_ok(tmp_path):
    p = _write(tmp_path, "real.pdf", _PDF)
    v = detect_media_mismatch(p)
    assert v.is_mismatch is False


def test_unsniffable_file_is_not_a_mismatch(tmp_path):
    # A .txt/.epub with no recognizable magic bytes -> can't prove wrong -> not flagged
    p = _write(tmp_path, "notes.txt", b"just some plain text, no magic")
    v = detect_media_mismatch(p)
    assert v.is_mismatch is False


def test_content_type_overrides_extension_for_declared(tmp_path):
    # declared via content-type (pdf) but bytes are HTML -> mismatch
    p = _write(tmp_path, "thing.bin", _HTML)
    v = detect_media_mismatch(p, content_type="application/pdf")
    assert v.is_mismatch is True
    assert v.declared == "pdf" and v.detected == "html"
