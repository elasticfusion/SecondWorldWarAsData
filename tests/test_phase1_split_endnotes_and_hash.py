"""PR4: M3 (split chapters must still write endnote refs) + M4 (unchanged-skip hash must
cover all provenance, not just paragraph text)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.models import MarkdownDocument, Paragraph, Footnote, Map
import phase1_parse as p1


def _doc(paras, footnotes=None, maps=None):
    return MarkdownDocument(
        book="St. Vith",
        chapter_number=2,
        chapter_title="Chapter Two",
        section_id="full",
        author="A",
        series="S",
        license="Public Domain",
        paragraphs=paras,
        footnotes=footnotes or [],
        maps=maps or [],
    )


def test_split_chapter_still_writes_endnote_refs(tmp_path: Path) -> None:
    """M3: a >400k chapter is split AND its endnote-refs artifact is still written
    (the early return previously skipped _save_endnote_refs for large chapters)."""
    big = "x" * 400
    paras = [
        Paragraph(absolute_number=i, text=big, section_id="full", source_file="c.md")
        for i in range(1200)
    ]
    fns = [Footnote(number=1, url="https://x/ref1", text="ref one")]
    doc = _doc(paras, footnotes=fns)
    logger = logging.getLogger("pr4")
    p1._process_document(doc, tmp_path, logger)
    # split chunks written
    assert list(tmp_path.glob("chapter2*c*-parsed.json")), "expected split chunks"
    # AND the endnote-refs artifact written (M3)
    endnotes = tmp_path / "chapter2full-endnotes.json"
    assert endnotes.exists(), "split chapter lost its endnote-refs artifact (M3)"
    data = json.loads(endnotes.read_text())
    assert data["endnotes"][0]["url"] == "https://x/ref1"


def test_unchanged_skip_hash_covers_new_map(tmp_path: Path) -> None:
    """M4: re-processing a doc whose prose is unchanged but that NEWLY extracts a map must
    NOT be skipped as 'unchanged' — the new provenance must be written."""
    paras = [
        Paragraph(
            absolute_number=0,
            text="identical prose",
            section_id="full",
            source_file="c.md",
        )
    ]
    logger = logging.getLogger("pr4")

    # First pass: no map.
    p1._process_document(_doc(paras), tmp_path, logger)
    out = tmp_path / "chapter2full-parsed.json"
    assert out.exists()
    assert json.loads(out.read_text())["maps"] == []

    # Second pass: same prose, but a map now extracted. Must re-write (not skip).
    p1._process_document(
        _doc(
            paras, maps=[Map(url="u", description="d", map_id="m1", paragraph_number=0)]
        ),
        tmp_path,
        logger,
    )
    reloaded = json.loads(out.read_text())
    assert len(reloaded["maps"]) == 1, "new map was skipped as 'unchanged' (M4)"
    assert reloaded["maps"][0]["map_id"] == "m1"
