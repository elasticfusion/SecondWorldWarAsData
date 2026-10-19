"""A000: bibliography title derivation + in-place backfill (published + archival/military refs)."""

import json
from pathlib import Path

from src.extraction.bibliography import _derive_bib_title, backfill_bib_titles


def test_derive_prefers_citation_title_then_alt_then_verbatim():
    assert _derive_bib_title({"title": "A Soldier's Story"}, {}) == "A Soldier's Story"
    # archival: no real title -> alt_title
    assert (
        _derive_bib_title(
            {"title": "Unknown", "alt_title": "V Corps Operations, p. 256"}, {}
        )
        == "V Corps Operations, p. 256"
    )
    # no citation title/alt -> mention verbatim
    assert (
        _derive_bib_title(
            {"title": "Unknown"}, {"verbatim_reference": "FUSA G-2 Estimate 24"}
        )
        == "FUSA G-2 Estimate 24"
    )
    # nothing usable
    assert _derive_bib_title({"title": "Unknown"}, {}) == "Unknown"


def test_backfill_repairs_in_place(tmp_path):
    bib = tmp_path / "bibliography"
    bib.mkdir()
    # archival record: top-level + citation.title Unknown, real text in alt_title
    (bib / "bib_aaa.json").write_text(
        json.dumps(
            {
                "BibliographyID": "01A",
                "title": "Unknown",
                "citation": {
                    "title": "Unknown",
                    "alt_title": "MS # B-810, Hans Schmidt",
                },
                "mentions": [{"verbatim_reference": "MS # B-810"}],
            }
        )
    )
    # already-titled published work: must be left untouched
    (bib / "bib_bbb.json").write_text(
        json.dumps(
            {
                "BibliographyID": "01B",
                "title": "A Soldier's Story",
                "citation": {"title": "A Soldier's Story"},
            }
        )
    )
    scanned, fixed = backfill_bib_titles(bib)
    assert scanned == 2 and fixed == 1
    a = json.loads((bib / "bib_aaa.json").read_text())
    assert a["title"] == "MS # B-810, Hans Schmidt"
    assert a["citation"]["title"] == "MS # B-810, Hans Schmidt"
    b = json.loads((bib / "bib_bbb.json").read_text())
    assert b["title"] == "A Soldier's Story"  # unchanged
