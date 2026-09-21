"""Tests for the docx / epub / txt -> markdown converters (requirement #6).

txt conversion is dependency-free and always tested. docx/epub go through
pandoc; those tests build a small source with pandoc and skip cleanly when the
pandoc binary is not installed, so the suite is portable.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from src.ingestion.media_detection import build_source_metadata, detect_media_type
from src.ingestion.text_converters import (
    ConverterUnavailable,
    convert_to_markdown,
    docx_to_markdown,
    epub_to_markdown,
    pandoc_available,
    txt_to_markdown,
)

_HAVE_PANDOC = shutil.which("pandoc") is not None
_needs_pandoc = pytest.mark.skipif(not _HAVE_PANDOC, reason="pandoc not installed")

_SRC_MD = (
    "# Heading\n\n"
    "A paragraph about **St. Vith**.\n\n"
    "| Unit | Strength |\n|------|----------|\n| CC-B | 2700 |\n"
)


def _make(tmp_path: Path, out_name: str) -> Path:
    """Build a docx/epub from markdown via pandoc for round-trip testing."""
    src = tmp_path / "src.md"
    src.write_text(_SRC_MD, encoding="utf-8")
    out = tmp_path / out_name
    cmd = ["pandoc", str(src), "-o", str(out)]
    if out_name.endswith(".epub"):
        cmd += ["--metadata", "title=Test"]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


# --- txt (no external dependency) ----------------------------------------


def test_txt_to_markdown(tmp_path: Path) -> None:
    p = tmp_path / "note.txt"
    p.write_text(
        "Section One\r\n\r\nThe 7th Armored moved out.\n\n\n", encoding="utf-8"
    )
    md = txt_to_markdown(p)
    assert "Section One" in md
    assert "\r" not in md  # newlines normalized
    assert md.endswith("\n")
    assert not md.endswith("\n\n\n")  # trailing blank run collapsed


def test_txt_detected_and_dispatched(tmp_path: Path) -> None:
    p = tmp_path / "note.txt"
    p.write_text("plain content", encoding="utf-8")
    assert detect_media_type(p) == "text"
    assert build_source_metadata("01T", p, with_checksum=False).supported is True
    assert convert_to_markdown(p, "text").startswith("plain content")


# --- docx / epub (pandoc) -------------------------------------------------


@_needs_pandoc
def test_docx_to_markdown_preserves_table(tmp_path: Path) -> None:
    out = _make(tmp_path, "doc.docx")
    assert detect_media_type(out) == "docx"
    md = docx_to_markdown(out)
    # pandoc may render spaces inside bold runs as non-breaking spaces; normalize.
    norm = md.replace("\xa0", " ")
    assert "Heading" in norm
    assert "**St. Vith**" in norm
    # table survives as GFM pipe table
    assert "CC-B" in norm and "2700" in norm
    assert "|" in norm


@_needs_pandoc
def test_epub_to_markdown(tmp_path: Path) -> None:
    out = _make(tmp_path, "book.epub")
    assert detect_media_type(out) == "epub"
    md = epub_to_markdown(out).replace("\xa0", " ")
    assert "Heading" in md
    assert "St. Vith" in md


@_needs_pandoc
def test_convert_dispatch_docx(tmp_path: Path) -> None:
    out = _make(tmp_path, "doc.docx")
    assert "St. Vith" in convert_to_markdown(out, "docx").replace("\xa0", " ")


# --- error handling -------------------------------------------------------


def test_unhandled_media_type_raises(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        convert_to_markdown(p, "pdf")


def test_missing_file_raises(tmp_path: Path) -> None:
    from src.ingestion.text_converters import ConverterError

    if not _HAVE_PANDOC:
        # Without pandoc, docx conversion raises unavailability first.
        with pytest.raises(ConverterUnavailable):
            docx_to_markdown(tmp_path / "nope.docx")
    else:
        with pytest.raises(ConverterError):
            docx_to_markdown(tmp_path / "nope.docx")
