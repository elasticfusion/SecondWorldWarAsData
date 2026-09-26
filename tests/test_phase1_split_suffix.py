"""Regression tests for phase1_parse large-chapter splitting.

Guards the section-suffix scheme against the >26-chunk overflow bug where
``chr(97 + idx)`` produced punctuation/non-ASCII filenames ({ | } ~ ...) and
could collide, corrupting large-document output.
"""

import logging
from pathlib import Path

from phase1_parse import _save_split_chapter
from src.models import MarkdownDocument, Paragraph


def _make_doc(num_paragraphs: int) -> MarkdownDocument:
    # Each paragraph large enough that the total exceeds the 400k split threshold.
    text = "x" * 400
    paras = [
        Paragraph(absolute_number=i, text=text, section_id="", source_file="c.md")
        for i in range(num_paragraphs)
    ]
    return MarkdownDocument(
        book="St. Vith",
        chapter_number=1,
        chapter_title="T",
        section_id="",
        author="A",
        series="S",
        license="Public Domain",
        paragraphs=paras,
    )


def test_split_chapter_suffixes_safe_beyond_26_chunks(tmp_path: Path) -> None:
    # 3264 paragraphs / (50-3) step -> ~70 chunks, well past 26.
    doc = _make_doc(3264)
    logger = logging.getLogger("test")

    did_split = _save_split_chapter(doc, tmp_path, logger)
    assert did_split is True

    files = sorted(p.name for p in tmp_path.glob("chapter1*-parsed.json"))
    assert len(files) > 26, "test should exercise the >26-chunk path"

    # 1) No collisions: filename count == unique count.
    assert len(files) == len(set(files))

    # 2) All filenames are pure ASCII (no { | } ~ or extended bytes).
    for name in files:
        assert name.isascii(), f"non-ASCII chunk filename: {name!r}"
        # numeric zero-padded suffix scheme: chapter1cNN-parsed.json
        assert name.startswith("chapter1c")

    # 3) Suffixes sort in chunk order (zero-padded width stable).
    assert files == sorted(files)
