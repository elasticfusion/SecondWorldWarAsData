"""PR1: footnotes/endnotes writes must go through the locked write-guard (not raw
json.dump) and must include the section suffix so subsections don't collide."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.models import MarkdownDocument, Paragraph, Footnote
import phase1_parse as p1


def _doc(
    chapter_title: str, section_id: str, paras, footnotes=None
) -> MarkdownDocument:
    return MarkdownDocument(
        book="St. Vith",
        chapter_number=6,
        chapter_title=chapter_title,
        section_id=section_id,
        author="A",
        series="S",
        license="Public Domain",
        paragraphs=paras,
        footnotes=footnotes or [],
    )


def _note_paras():
    return [
        Paragraph(
            absolute_number=0,
            text="1. First note text",
            section_id="",
            source_file="c.md",
        ),
        Paragraph(
            absolute_number=1,
            text="2. Second note text",
            section_id="",
            source_file="c.md",
        ),
    ]


def test_footnotes_written_through_guard_with_metadata(tmp_path: Path) -> None:
    """B1: _save_footnotes_chapter routes through write_json_with_lock -> the file
    carries the guard's _schema_version/_last_updated stamp (a raw json.dump would not).
    """
    doc = _doc("Notes", "a", _note_paras())
    logger = logging.getLogger("pr1")
    assert p1._save_footnotes_chapter(doc, "a", tmp_path, logger) is True
    f = tmp_path / "chapter6a-footnotes.json"
    assert f.exists()
    data = json.loads(f.read_text())
    assert "_schema_version" in data and "_last_updated" in data  # guard stamp
    assert len(data["footnotes"]) == 2


def test_footnotes_subsections_do_not_collide(tmp_path: Path) -> None:
    """B3: two footnotes subsections (6a, 6b) of the same chapter write DISTINCT files
    (the old suffix-less name silently overwrote)."""
    logger = logging.getLogger("pr1")
    p1._save_footnotes_chapter(_doc("Notes", "a", _note_paras()), "a", tmp_path, logger)
    p1._save_footnotes_chapter(_doc("Notes", "b", _note_paras()), "b", tmp_path, logger)
    files = sorted(f.name for f in tmp_path.glob("chapter6*-footnotes.json"))
    assert files == ["chapter6a-footnotes.json", "chapter6b-footnotes.json"], files


def test_endnote_refs_written_through_guard(tmp_path: Path) -> None:
    """B2: _save_endnote_refs routes through write_json_with_lock (metadata stamp present)."""
    fns = [Footnote(number=1, url="https://x/y", text="ref one")]
    doc = _doc(
        "Chapter",
        "full",
        [Paragraph(absolute_number=0, text="body", section_id="", source_file="c.md")],
        footnotes=fns,
    )
    logger = logging.getLogger("pr1")
    p1._save_endnote_refs(doc, "full", tmp_path, logger)
    f = tmp_path / "chapter6full-endnotes.json"
    assert f.exists()
    data = json.loads(f.read_text())
    assert "_schema_version" in data
    assert data["endnotes"][0]["url"] == "https://x/y"
