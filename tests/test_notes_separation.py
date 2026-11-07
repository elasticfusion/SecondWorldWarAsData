"""Phase 1 separates a chapter's footnotes/endnotes from its narrative, keeping provenance.

XChannel-8 E2E: the converted HyperWar chapter kept its notes as raw HTML anchors that no Phase 1
regex recognised, so 0 of 102 notes were parsed and the whole notes section (+ page navigation)
became ONE 27k-character "narrative" paragraph that every Phase 2 prompt carried (yield guard
failure). src/notes.separate_notes must cut the section out of the body, canonicalise in-text
markers, and return each note with number / kind / text / url / citing paragraph.

The regression fixture ``fixtures/notes/USA-E-XChannel-8.md.gz`` is the pandoc conversion of
https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html (G. A. Harrison,
Cross-Channel Attack, ch. 8 - U.S. Army Center of Military History, public domain).
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import notes as N

BASE = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html"
FIXTURE = Path(__file__).parent / "fixtures" / "notes" / "USA-E-XChannel-8.md.gz"


def _html_chapter(heading: str = "### Footnotes") -> str:
    return (
        '> First paragraph of narrative.<sup><a href="#fn1" id="cn1">1</a></sup>\n'
        ">\n"
        "> Second paragraph cites two notes.<sup><a href=#fn2 id=cn2>2</a></sup> More text."
        "<sup> <a href='#fn3' id='cn3'> 3 </a> </sup>\n\n"
        "------------------------------------------------------------------------\n\n"
        "> [Table of Contents](index.html) \\*\\* [Previous Chapter](ch-7.html) (7) \\* "
        "[Next Chapter](ch-9.html) (9)\n"
        ">\n"
        f"> {heading}\n"
        ">\n"
        '> \\[<a href="#cn1" id="fn1">1</a>\\] 116th Inf S-3 Jnl.\n'
        ">\n"
        '> \\[<a href="#cn2" id="fn2">2</a>\\] MS \\# B-432 (Ziegelmann). See also\n'
        "> \\[Clifford Jones\\] NEPTUNE, MS, pp. 275ff.\n"
        ">\n"
        '> \\[<a href="#cn3" id="fn3">3</a>\\] Ibid.\n\n'
        "------------------------------------------------------------------------\n\n"
        "Transcribed and formatted for HTML by Patrick Clancey, HyperWar Foundation\n"
    )


# --- separate_notes: HTML-anchor form ----------------------------------------------------------


def test_html_notes_are_parsed_with_provenance():
    body, notes = N.separate_notes(_html_chapter(), BASE)
    assert [n.number for n in notes] == [1, 2, 3]
    assert notes[0].text == "116th Inf S-3 Jnl."  # case 1: straight reference
    assert notes[2].text == "Ibid."
    assert all(n.kind == "footnote" for n in notes)
    assert notes[1].url == f"{BASE}#fn2"


def test_multiline_note_joins_continuation_and_unescapes_pandoc():
    _body, notes = N.separate_notes(_html_chapter(), BASE)
    # continuation line joined; "\#" and "\[...\]" conversion escapes removed
    assert notes[1].text == (
        "MS # B-432 (Ziegelmann). See also [Clifford Jones] NEPTUNE, MS, pp. 275ff."
    )


def test_body_loses_notes_section_and_navigation_but_keeps_trailer():
    body, _notes = N.separate_notes(_html_chapter(), BASE)
    assert "Footnotes" not in body
    assert "116th Inf S-3 Jnl" not in body
    assert "Table of Contents" not in body and "Next Chapter" not in body
    assert "First paragraph of narrative." in body
    # the section ends at the unquoted rule: what follows it is not swallowed
    assert "Transcribed and formatted" in body


def test_markers_are_canonicalised_to_the_one_form():
    body, _notes = N.separate_notes(_html_chapter(), BASE)
    assert "<a href" not in body
    assert f"<sup>[1]({BASE}#fn1)</sup>" in body
    assert f"<sup>[2]({BASE}#fn2)</sup>" in body
    assert f"<sup>[3]({BASE}#fn3)</sup>" in body  # spaced / single-quoted variant too


def test_canonical_marker_without_http_base_has_no_fake_url():
    out = N.canonicalize_markers(
        'x<sup><a href="#fn7" id="cn7">7</a></sup>', "converted"
    )
    assert out == "x<sup>[7]</sup>"
    _body, notes = N.separate_notes(_html_chapter(), "")
    assert all(n.url == "" for n in notes)  # never invent a URL


def test_base_url_fragment_is_dropped_from_note_url():
    _body, notes = N.separate_notes(_html_chapter(), BASE + "#top")
    assert notes[0].url == f"{BASE}#fn1"


@pytest.mark.parametrize(
    "heading,kind",
    [
        ("### Footnotes", "footnote"),
        ("## Endnotes", "endnote"),
        ("#### **Notes**", "endnote"),
    ],
)
def test_kind_follows_the_sources_own_heading(heading, kind):
    _body, notes = N.separate_notes(_html_chapter(heading), BASE)
    assert notes and {n.kind for n in notes} == {kind}


# --- separate_notes: markdown-link form ---------------------------------------------------------


def test_markdown_definition_form_is_parsed():
    md = (
        "Narrative text.<sup>[1](https://h/p.html#fn1)</sup>\n\n"
        "## Notes\n\n"
        "[1](https://h/p.html#cn1). *Seventh Army, KTB 1.I.-30.VI.44,* 6 Jun 44.\n"
    )
    body, notes = N.separate_notes(md, "https://h/p.html")
    assert len(notes) == 1 and notes[0].kind == "endnote"
    assert notes[0].text == "*Seventh Army, KTB 1.I.-30.VI.44,* 6 Jun 44."
    assert "KTB" not in body and "Narrative text." in body


def test_heading_without_definitions_is_not_cut():
    # A "Notes" heading that holds prose (no note definitions) is narrative: keep it.
    md = "Intro.\n\n## Notes\n\nThese are the author's general notes on sources.\n"
    body, notes = N.separate_notes(md, BASE)
    assert notes == []
    assert "general notes on sources" in body


def test_no_notes_section_returns_body_unchanged_except_markers():
    md = "Plain text with no notes at all.\n"
    assert N.separate_notes(md, BASE) == (md, [])


def test_last_notes_section_wins():
    md = (
        "## Notes\n\nAn early heading of prose.\n\n"
        'Body.<sup><a href="#fn1" id="cn1">1</a></sup>\n\n'
        '### Footnotes\n\n\\[<a href="#cn1" id="fn1">1</a>\\] Ibid.\n'
    )
    body, notes = N.separate_notes(md, BASE)
    assert [n.text for n in notes] == ["Ibid."]
    assert "An early heading of prose." in body


# --- attach_citing_paragraphs -------------------------------------------------------------------


def _paras(*texts):
    return [
        SimpleNamespace(text=t, absolute_number=10 + i) for i, t in enumerate(texts)
    ]


def test_notes_attach_to_first_citing_paragraph():
    notes = [
        N.Note(1, "footnote", "a"),
        N.Note(2, "footnote", "b"),
        N.Note(9, "footnote", "c"),
    ]
    located = N.attach_citing_paragraphs(
        notes, _paras("x<sup>[1](u)</sup>", "y<sup>[2]</sup> z<sup>[1](u)</sup>")
    )
    assert located == 2
    assert [n.paragraph_number for n in notes] == [
        10,
        11,
        0,
    ]  # 9 not cited -> 0 (unknown)


# --- real chapter regression --------------------------------------------------------------------


@pytest.fixture(scope="module")
def chapter() -> str:
    return gzip.decompress(FIXTURE.read_bytes()).decode("utf-8")


def test_real_chapter_all_102_notes_separated(chapter):
    body, notes = N.separate_notes(chapter, BASE)
    assert [n.number for n in notes] == list(range(1, 103))
    assert all(n.text and n.kind == "footnote" for n in notes)
    by = {n.number: n.text for n in notes}
    assert by[76] == "MS # B-432 (Ziegelmann)."  # pure reference
    assert by[75].startswith("Reasons for this failure")  # informational (case 2)
    assert "Table of Contents" not in body
    assert '<a href="#fn' not in body and 'id="fn' not in body


def test_real_chapter_parse_has_no_giant_paragraph(tmp_path, chapter):
    from src.models import Metadata
    from src.parser import parse_content_file

    src = tmp_path / "USA-E-XChannel-8.md"
    src.write_text(chapter, encoding="utf-8")
    meta = Metadata(
        book="USA-E-XChannel",
        chapter_title="The Sixth of June",
        author="Harrison",
        series="",
        license="",
        source_url=BASE,
    )
    doc = parse_content_file(src, section_id="a", start_paragraph_num=1, metadata=meta)
    assert len(doc.footnotes) == 102
    assert (
        max(len(p.text) for p in doc.paragraphs) < 5000
    )  # was one 27,363-char paragraph
    assert all(f.paragraph_number > 0 for f in doc.footnotes)  # every note anchored
    assert not any("116th Inf S-3 Jnl." == p.text.strip() for p in doc.paragraphs)
    assert len(doc.maps) == 11


# --- parser wiring ------------------------------------------------------------------------------


def _doc(paras):
    from src.models import MarkdownDocument

    doc = MarkdownDocument.__new__(MarkdownDocument)
    doc.book = "B"
    doc.footnotes = []
    doc.images = []
    doc.paragraphs = _paras(*paras)
    return doc


def test_add_footnotes_falls_back_to_legacy_markdown_links(monkeypatch):
    from src import parser as P

    monkeypatch.setattr(
        P, "extract_footnotes", lambda content: [(4, "https://h/p#fn4", "Legacy note.")]
    )
    doc = _doc(["Cites<sup>[4](https://h/p#fn4)</sup>"])
    P._add_footnotes_to_doc(doc, "ignored", notes=[])
    (f,) = doc.footnotes
    assert (f.number, f.text, f.kind, f.paragraph_number) == (
        4,
        "Legacy note.",
        "footnote",
        10,
    )


def test_unanchored_notes_raise_an_actionable_alert(caplog):
    from src import parser as P
    from src.utils.observe import reset_counted_warnings

    reset_counted_warnings()
    doc = _doc(["No markers here."])
    doc.chapter_number = 8
    P._add_footnotes_to_doc(doc, "", notes=[N.Note(1, "endnote", "Ibid.")])
    with caplog.at_level(logging.WARNING):
        P._link_chapter_notes([doc], "B")
    assert doc.footnotes[0].paragraph_number == 0
    assert any("notes_unanchored" in r.getMessage() for r in caplog.records)


def test_orphan_markers_raise_an_actionable_alert(caplog):
    # The original failure: markers in the text, notes section not recognised -> must be LOUD.
    from src import parser as P
    from src.utils.observe import reset_counted_warnings

    reset_counted_warnings()
    doc = _doc(["Cites<sup>[5](u)</sup> and<sup>[6]</sup>"])
    doc.chapter_number = 8
    with caplog.at_level(logging.WARNING):
        P._link_chapter_notes([doc], "B")
    msgs = [
        r.getMessage()
        for r in caplog.records
        if "notes_orphan_markers" in r.getMessage()
    ]
    assert msgs and "5" in msgs[0] and "6" in msgs[0]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_multi_section_chapter_links_notes_cited_in_earlier_files(
    tmp_path, monkeypatch, caplog
):
    # A chapter split across files: markers in section a, the notes section only in section b.
    # Per-file linking alone would leave note 1 unanchored and raise a FALSE alert.
    from src import parser as P
    from src.models import ChapterGroup, Metadata
    from src.utils.observe import reset_counted_warnings

    reset_counted_warnings()
    meta = Metadata(
        book="B", chapter_title="T", author="A", series="", license="", source_url=BASE
    )
    monkeypatch.setattr(P, "parse_metadata", lambda _f: meta)
    a = _write(
        tmp_path / "chapter8a-content.md",
        '> Opening paragraph of the chapter, long enough.<sup><a href="#fn1" id="cn1">1</a>'
        "</sup>\n",
    )
    b = _write(
        tmp_path / "chapter8b-content.md",
        '> Closing paragraph of the chapter, long enough.<sup><a href="#fn2" id="cn2">2</a>'
        '</sup>\n\n> ### Footnotes\n>\n> \\[<a href="#cn1" id="fn1">1</a>\\] Ibid.\n>\n'
        '> \\[<a href="#cn2" id="fn2">2</a>\\] 116th Inf S-3 Jnl.\n',
    )
    group = ChapterGroup(
        book="B",
        chapter_number=8,
        meta_file=tmp_path / "m.yaml",
        content_files={"a": a, "b": b},
    )
    with caplog.at_level(logging.WARNING):
        docs = P.parse_chapter(group)
    notes = {f.number: f for d in docs for f in d.footnotes}
    assert notes[1].paragraph_number == docs[0].paragraphs[0].absolute_number
    assert notes[2].paragraph_number == docs[1].paragraphs[0].absolute_number
    assert not any("notes_" in r.getMessage() for r in caplog.records)  # no false alert


def test_duplicate_definition_keeps_first_and_reports():
    md = (
        "### Footnotes\n\n"
        '\\[<a href="#cn1" id="fn1">1</a>\\] First text.\n\n'
        '\\[<a href="#cn1" id="fn1">1</a>\\] Second text.\ncontinued.\n'
    )
    _body, notes, sep = N.separate_notes_detailed(md, BASE)
    assert [n.text for n in notes] == [
        "First text."
    ]  # never merged into one false note
    assert sep.duplicates == [1]


def test_duplicate_definition_alerts_from_parse(tmp_path, caplog):
    from src.models import Metadata
    from src.parser import parse_content_file
    from src.utils.observe import reset_counted_warnings

    reset_counted_warnings()
    src = _write(
        tmp_path / "chapter1-content.md",
        "Body text of the chapter that is long enough.<sup>[1]</sup>\n\n### Notes\n\n"
        '\\[<a href="#cn1" id="fn1">1</a>\\] A.\n\n\\[<a href="#cn1" id="fn1">1</a>\\] B.\n',
    )
    meta = Metadata(book="B", chapter_title="T", author="", series="", license="")
    with caplog.at_level(logging.WARNING):
        parse_content_file(src, section_id="a", start_paragraph_num=1, metadata=meta)
    assert any("notes_duplicate_definition" in r.getMessage() for r in caplog.records)


def test_later_prose_notes_heading_does_not_hide_real_section():
    md = (
        'Body.<sup><a href="#fn1" id="cn1">1</a></sup>\n\n'
        '### Footnotes\n\n\\[<a href="#cn1" id="fn1">1</a>\\] Ibid.\n\n'
        "------------------------------------------------------------------------\n\n"
        "## Notes on Sources\n\n## Notes\n\nThe author's general remarks on sources.\n"
    )
    body, notes = N.separate_notes(md, BASE)
    assert [n.text for n in notes] == ["Ibid."]
    assert "Footnotes" not in body
    assert "general remarks on sources" in body


def test_note_starting_with_bold_does_not_end_section():
    md = (
        "### Footnotes\n\n"
        '\\[<a href="#cn1" id="fn1">1</a>\\] A.\n\n'
        '\\[<a href="#cn2" id="fn2">2</a>\\] B.\n***bold*** continuation.\n'
    )
    _body, notes = N.separate_notes(md, BASE)
    assert [n.number for n in notes] == [1, 2]
    assert notes[1].text == "B. ***bold*** continuation."


def test_single_nav_link_in_narrative_is_kept():
    md = "See the [Table of Contents](index.html) for the full list of chapters.\n"
    body, _notes, sep = N.separate_notes_detailed(md, BASE)
    assert body == md and sep.nav_lines_removed == 0


def test_markdown_definition_supplies_url_when_base_is_unknown():
    md = "## Notes\n\n[3](https://h/p.html#cn3). Ibid.\n"
    _body, notes = N.separate_notes(md, "converted from PDF")
    assert notes[0].url == "https://h/p.html#fn3"


def test_images_anchor_against_the_separated_body(tmp_path, monkeypatch):
    # Paragraph text is canonicalised; anchoring against the raw content misses a paragraph whose
    # leading words contain a marker and attaches the image to the PREVIOUS paragraph.
    from src import parser as P
    from src.models import Metadata

    raw = (
        "Opening paragraph that is long enough to probe properly.\n\n"
        'Second<sup><a href="#fn1" id="cn1">1</a></sup> paragraph text here.\n'
        "![fig](https://h/img/fig.jpg)\n\n"
        "Third paragraph closes the chapter text.\n\n"
        '### Footnotes\n\n\\[<a href="#cn1" id="fn1">1</a>\\] Ibid.\n'
    )
    body, _notes = N.separate_notes(raw, BASE)
    doc = _doc([])
    doc.images = []
    doc.paragraphs = [
        SimpleNamespace(text=t.strip(), absolute_number=i + 1)
        for i, t in enumerate(body.split("\n\n")[:3])
    ]
    P._add_images_to_doc(
        doc, raw, base_url=BASE
    )  # anchored against raw: wrong paragraph
    assert [i.paragraph_number for i in doc.images] == [1]
    doc.images = []
    P._add_images_to_doc(doc, raw, base_url=BASE, anchor_content=body)
    assert [i.paragraph_number for i in doc.images] == [2]

    # parse_content_file passes the BODY as the anchor text
    seen = {}
    real = P._anchor_images_to_paragraphs
    monkeypatch.setattr(
        P,
        "_anchor_images_to_paragraphs",
        lambda d, c, n=None: (seen.setdefault("c", c), real(d, c, n)),
    )
    src = _write(tmp_path / "chapter1-content.md", raw)
    meta = Metadata(
        book="B", chapter_title="T", author="", series="", license="", source_url=BASE
    )
    P.parse_content_file(src, section_id="a", start_paragraph_num=1, metadata=meta)
    assert seen["c"] == body


def test_bibliography_reference_type_follows_note_kind(tmp_path):
    import json

    from src.extraction.bibliography import _collect_parsed_notes

    pf = tmp_path / "x-parsed.json"
    pf.write_text(
        json.dumps(
            {
                "footnotes": [
                    {"number": 1, "text": "a", "kind": "endnote"},
                    {"number": 2, "text": "b", "kind": "footnote"},
                    {"number": 3, "text": "c"},  # pre-kind record -> array key
                ],
                "endnotes": [{"number": 4, "text": "d"}],
            }
        ),
        encoding="utf-8",
    )
    got = {n["number"]: n["_ref_type"] for n in _collect_parsed_notes(pf)}
    assert got == {1: "endnote", 2: "footnote", 3: "footnote", 4: "endnote"}


def test_footnote_to_dict_is_the_one_serialized_form():
    from src.models import Footnote

    d = Footnote(
        number=3, url="u", paragraph_number=7, text="t", kind="endnote"
    ).to_dict()
    assert d == {
        "number": 3,
        "url": "u",
        "text": "t",
        "kind": "endnote",
        "paragraph_number": 7,
    }


def test_serialisers_emit_kind_and_paragraph(tmp_path):
    # DRY guard: every Phase 1 serialiser emits notes via Footnote.to_dict (kind + paragraph kept).
    from lambda_handlers.phase1_handler import _doc_to_output_minimal
    from phase1_parse import _doc_to_dict
    from src.models import Metadata
    from src.parser import parse_content_file

    src = _write(tmp_path / "chapter1-content.md", _html_chapter("## Endnotes"))
    meta = Metadata(
        book="B", chapter_title="T", author="", series="", license="", source_url=BASE
    )
    doc = parse_content_file(src, section_id="a", start_paragraph_num=1, metadata=meta)
    expected = [f.to_dict() for f in doc.footnotes]
    assert (
        expected
        and expected[0]["kind"] == "endnote"
        and expected[0]["paragraph_number"]
    )
    assert _doc_to_dict(doc)["footnotes"] == expected
    assert _doc_to_output_minimal(doc)["footnotes"] == expected


# --- downstream readers -------------------------------------------------------------------------


def test_events_note_context_classifies_by_kind():
    from src.extraction.events import _parse_note_context

    _lookup, fn, en = _parse_note_context(
        {
            "paragraphs": [],
            "footnotes": [
                {"number": 1, "kind": "footnote"},
                {"number": 2, "kind": "endnote"},
                {"number": 3},  # legacy record without kind -> footnote
            ],
            "endnotes": [
                {"number": 9}
            ],  # legacy separate endnotes array still honoured
        }
    )
    assert fn == {1, 3}
    assert en == {2, 9}


def test_phase2_summary_counts_source_sections_in_real_layout(tmp_path):
    from phase2_extract import _log_phase2_summary

    d = tmp_path / "content" / "USA-E-XChannel" / "source_section"
    d.mkdir(parents=True)
    (d / "01ABC.json").write_text("{}", encoding="utf-8")
    counts = _log_phase2_summary(tmp_path, logging.getLogger("t"))
    assert (
        counts["source_section"] == 1
    )  # was a false 0 (counted output/source_section)


# --- images: relative source paths, note-linked images, one serialized form ------------------


def test_relative_image_paths_anchor_against_absolute_urls():
    # Image URLs are resolved to absolute; the source keeps "img/X.jpg". The absolute form alone
    # never matched: XChannel-8 had 39/39 images unanchored (no provenance to a paragraph).
    from src import parser as P

    content = (
        "Opening paragraph that is long enough to probe properly.\n\n"
        "![](img/fig-1.jpg)\n\n"
        "Second paragraph of the chapter, also long enough.\n\n"
        '<a href="img/fig-2.jpg"><img src="icn/fig-2.jpg" /></a>\n'
    )
    doc = _doc([])
    doc.source_url = "https://h/book/ch-8.html"
    doc.paragraphs = [
        SimpleNamespace(text=t, absolute_number=i + 1)
        for i, t in enumerate(content.strip().split("\n\n"))
    ]
    P._add_images_to_doc(doc, content, base_url=doc.source_url)
    assert [i.url for i in doc.images][:1] == ["https://h/book/img/fig-1.jpg"]
    assert all(i.paragraph_number for i in doc.images)


def test_real_chapter_every_image_anchored_and_note_image_linked(tmp_path, chapter):
    from src.models import Metadata
    from src.parser import parse_content_file

    src = tmp_path / "USA-E-XChannel-8.md"
    src.write_text(chapter, encoding="utf-8")
    meta = Metadata(
        book="USA-E-XChannel",
        chapter_title="The Sixth of June",
        author="",
        series="",
        license="",
        source_url=BASE,
    )
    doc = parse_content_file(src, section_id="a", start_paragraph_num=1, metadata=meta)
    assert len(doc.images) == 39
    assert all(i.paragraph_number for i in doc.images)
    # the naval command chart is linked only from note 23 -> the paragraph citing note 23
    (chart,) = [i for i in doc.images if i.note_number]
    note23 = next(f for f in doc.footnotes if f.number == 23)
    assert chart.url.endswith("charts/USA-E-XChannel-4.jpg") and chart.note_number == 23
    assert chart.paragraph_number == note23.paragraph_number > 0


def test_note_linked_image_takes_chapter_wide_citing_paragraph():
    # Note cited in an EARLIER section: per-file the note (and its image) has paragraph 0;
    # chapter-level linking must fill both.
    from src import parser as P
    from src.models import Footnote, Image

    first, last = _doc(["Cites<sup>[23](u)</sup> here."]), _doc(["Closing text."])
    last.paragraphs[0].absolute_number = 50
    for d in (first, last):
        d.chapter_number = 8
    last.footnotes = [
        Footnote(number=23, url="u", paragraph_number=0, text="See chart")
    ]
    last.images = [Image(type="external", url="https://h/c.jpg", note_number=23)]
    P._link_chapter_notes([first, last], "B")
    assert last.footnotes[0].paragraph_number == 10
    assert last.images[0].paragraph_number == 10


def test_parent_relative_image_path_anchors():
    # "../img/X.jpg" is derived exactly from URL + base (relpath), not guessed from the basename.
    from src import parser as P
    from src.models import Image

    img = Image(type="external", url="https://h/a/img/X.jpg")
    assert P._image_tokens(img, "https://h/a/b/ch.html") == [
        "https://h/a/img/X.jpg",
        "/a/img/X.jpg",
        "../img/X.jpg",
    ]
    assert P._image_tokens(img, "converted from PDF") == ["https://h/a/img/X.jpg"]
    assert P._image_tokens(img, "https://h") == [
        "https://h/a/img/X.jpg",
        "/a/img/X.jpg",
        "a/img/X.jpg",
        "./a/img/X.jpg",
    ]


def test_same_basename_in_other_directory_is_not_a_match():
    # icn/X.jpg (thumbnail) and img/X.jpg (full image) share a basename: never conflate them.
    from src import parser as P

    assert P._image_pos("img/X.jpg", 'see <img src="icn/X.jpg">', 0) == -1
    assert P._image_pos("img/X.jpg", 'x <a href="other/img/X.jpg">', 0) == -1
    assert P._image_pos("img/X.jpg", 'x <a href="img/X.jpg">', 0) == 11
    assert P._image_pos("img/X.jpg", "![](img/X.jpg)", 0) == 4


def test_leading_image_is_not_alerted(caplog):
    # A frontispiece before all text stays 0 BY DESIGN: no "not found" alert (it was found).
    from src import parser as P

    content = (
        "![](img/front.jpg)\n\nFirst paragraph of the chapter that is long enough.\n"
    )
    doc = _doc([])
    doc.source_url = "https://h/book/ch.html"
    doc.paragraphs = [
        SimpleNamespace(
            text="First paragraph of the chapter that is long enough.",
            absolute_number=1,
        )
    ]
    with caplog.at_level(logging.INFO):
        P._add_images_to_doc(doc, content, base_url=doc.source_url)
    assert doc.images[0].paragraph_number == 0
    msgs = [r.getMessage() for r in caplog.records]
    assert any("image_leading" in m for m in msgs)
    assert not any("image_unanchored" in m for m in msgs)


def test_missing_image_is_alerted_with_reason(caplog):
    from src import parser as P
    from src.models import Image

    doc = _doc(["A paragraph that is long enough to be probed in the source."])
    doc.source_url = "https://h/book/ch.html"
    doc.images = [Image(type="external", url="https://h/book/img/gone.jpg")]
    with caplog.at_level(logging.WARNING):
        P._anchor_images_to_paragraphs(
            doc, "A paragraph that is long enough to be probed in the source."
        )
    (msg,) = [
        r.getMessage() for r in caplog.records if "image_unanchored" in r.getMessage()
    ]
    assert "_image_tokens" in msg


def test_restarted_numbering_keeps_local_citation():
    # Two files each with their own note 1: each note keeps the paragraph in ITS file.
    from src import parser as P
    from src.models import Footnote

    a, b = _doc(["A<sup>[1](u)</sup> text."]), _doc(["B<sup>[1](u)</sup> text."])
    b.paragraphs[0].absolute_number = 20
    for d in (a, b):
        d.chapter_number = 8
    a.footnotes = [Footnote(number=1, url="u", paragraph_number=10, text="a")]
    b.footnotes = [Footnote(number=1, url="u", paragraph_number=20, text="b")]
    P._link_chapter_notes([a, b], "B")
    assert [a.footnotes[0].paragraph_number, b.footnotes[0].paragraph_number] == [
        10,
        20,
    ]


def test_image_serialisers_share_to_dict(tmp_path):
    from lambda_handlers.phase1_handler import _doc_to_output_minimal
    from phase1_parse import _doc_to_dict
    from src.models import Metadata
    from src.parser import parse_content_file

    src = _write(
        tmp_path / "chapter1-content.md",
        "Opening paragraph that is long enough to probe properly.\n\n![](img/fig-1.jpg)\n",
    )
    meta = Metadata(
        book="B", chapter_title="T", author="", series="", license="", source_url=BASE
    )
    doc = parse_content_file(src, section_id="a", start_paragraph_num=1, metadata=meta)
    expected = [i.to_dict() for i in doc.images]
    assert expected and {"paragraph_number", "note_number"} <= set(expected[0])
    assert _doc_to_dict(doc)["images"] == expected
    assert (
        _doc_to_output_minimal(doc)["images"] == expected
    )  # Lambda used to drop the anchor


# --- image reference forms (final review) -------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "<img src=img/X.jpg>",  # unquoted attribute closed by '>'
        "![](<img/X.jpg>)",  # angle-bracketed markdown destination
        "![](./img/X.jpg)",  # './' collapsed by urljoin
        '[t](img/X.jpg "Title")',
        "src='img/X.jpg'",
        "![](https://h/book/img/X.jpg)",
        "![](/book/img/X.jpg)",  # root-relative
    ],
)
def test_written_forms_are_located(source):
    from src import parser as P
    from src.models import Image

    img = Image(type="external", url="https://h/book/img/X.jpg")
    tokens = P._image_tokens(img, "https://h/book/ch.html")
    assert any(P._image_pos(t, source, 0) != -1 for t in tokens)


def test_host_comparison_is_case_insensitive():
    from src import parser as P
    from src.models import Image

    img = Image(type="external", url="https://WWW.H.org/book/img/X.jpg")
    assert "img/X.jpg" in P._image_tokens(img, "https://www.h.org/book/ch.html")


def test_joplin_resource_is_located():
    from src import parser as P
    from src.models import Image

    img = Image(type="embedded", resource_id="/abc123")
    assert any(
        P._image_pos(t, "![x](:/abc123)", 0) != -1 for t in P._image_tokens(img, "")
    )


def test_malformed_urls_do_not_abort_the_parse(caplog):
    from src import parser as P
    from src.models import Image

    img = Image(type="external", url="https://[x/a.jpg")
    with caplog.at_level(logging.WARNING):
        tokens = P._image_tokens(img, "http://[bad")
    assert tokens == ["https://[x/a.jpg"]  # exact text still locates it
    assert any("image_url_malformed" in r.getMessage() for r in caplog.records)


def test_file_without_probeable_paragraphs_is_not_alerted(caplog):
    # An image-only page: nothing to locate and nothing to fix -> INFO, not a WARNING.
    from src import parser as P
    from src.models import Image

    doc = _doc(["Fig. 3"])
    doc.source_url = "https://h/book/ch.html"
    doc.images = [Image(type="external", url="https://h/book/img/X.jpg")]
    with caplog.at_level(logging.INFO):
        P._anchor_images_to_paragraphs(doc, "Fig. 3\n\n![](img/X.jpg)\n")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("image_leading" in m and "unprobeable=1" in m for m in msgs)
    assert not any("image_unanchored" in m for m in msgs)


def test_body_image_is_not_reassigned_to_a_note():
    # A leading image found in the body stays 0 even if a note also links it.
    from src import parser as P
    from src.models import Footnote, Image

    doc = _doc(["A paragraph long enough to be located in the source text."])
    doc.source_url = "https://h/book/ch.html"
    doc.images = [Image(type="external", url="https://h/book/img/X.jpg")]
    note = Footnote(
        number=1, url="", paragraph_number=10, text='see <a href="img/X.jpg">'
    )
    P._anchor_images_to_paragraphs(
        doc,
        "![](img/X.jpg)\n\nA paragraph long enough to be located in the source text.\n",
        [note],
    )
    assert (doc.images[0].paragraph_number, doc.images[0].note_number) == (0, 0)
