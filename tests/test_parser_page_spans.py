"""Parser page provenance for converted HTML: pandoc writes a page anchor as
``<span id="pageN"></span>`` (the parser only read ``<a id="pageN"></a>``), a rejoined paragraph
can carry a page marker inside it, and it can END on a later page than it starts."""

from __future__ import annotations

from pathlib import Path

from src.models import Paragraph
from src.notes import separate_notes
from src.parser import (
    _create_paragraphs,
    _paginate,
    _page_markers_for,
    extract_page_markers,
    split_into_blocks,
)


def _paras(content: str):
    page_content, page_map, markers = _paginate(content)
    blocks = split_into_blocks(content)
    paras = _create_paragraphs(blocks, 1, "full", Path("c.md"), page_content, page_map)
    return paras, markers


def test_span_and_anchor_page_markers_are_both_read():
    text = '<span id="page4"></span>x <a id="page5"></a>y'
    assert [(p, k) for _pos, p, k in extract_page_markers(text)] == [
        (4, "anchor"),
        (5, "anchor"),
    ]


def test_marker_text_is_removed_from_paragraphs():
    paras, _ = _paras('<span id="page3"></span>\n\nAlpha <span id="page4"></span>beta.')
    assert [p.text for p in paras] == ["Alpha beta."]


def test_a_paragraph_crossing_a_page_records_both_pages():
    content = (
        '<span id="page3"></span>\n\nShort one.\n\n'
        'The troops <span id="page4"></span>reached the river at dawn and dug in.\n\n'
        "Next paragraph on page four."
    )
    paras, markers = _paras(content)
    assert [(p.page_number, p.page_end) for p in paras] == [
        (3, None),
        (3, 4),
        (4, None),
    ]
    marks = _page_markers_for(markers, paras)
    assert [(m.page_number, m.paragraph_number) for m in marks] == [(3, 1), (4, 2)]


def test_a_marker_in_the_first_60_characters_still_locates_the_paragraph(caplog):
    content = '<span id="page1"></span>\n\nAb <span id="page2"></span>cd ' + "x" * 80
    paras, _ = _paras(content)
    assert paras[0].page_number == 1 and paras[0].page_end == 2
    assert "page_unlocated" not in caplog.text


def test_page_markers_with_no_paragraph_point_to_zero():
    marks = _page_markers_for([(9, "anchor")], [Paragraph(1, "t", page_number=1)])
    assert marks[0].paragraph_number == 0


def test_phase1_writes_page_end_only_when_a_paragraph_crosses_a_page():
    from phase1_parse import _paragraph_dict

    assert "page_end" not in _paragraph_dict(Paragraph(1, "t", page_number=3))
    assert (
        _paragraph_dict(Paragraph(2, "t", page_number=3, page_end=4))["page_end"] == 4
    )


def test_a_note_runs_to_the_next_definition_across_blank_lines():
    content = (
        'Body.<sup><a href="#fn1" id="cn1">1</a></sup>\n\n### Footnotes\n\n'
        '\\[<a href="#cn1" id="fn1">1</a>\\] The plan was:\n\n'
        "> 1.  First point.\n>\n> 2.  Second point.\n\nMemo cited.\n\n"
        '\\[<a href="#cn2" id="fn2">2</a>\\] Second note.\n'
    )
    _body, notes = separate_notes(content, "https://example.org/x.html")
    by = {n.number: n.text for n in notes}
    assert by[1] == "The plan was:\n1.  First point.\n2.  Second point.\nMemo cited."
    assert by[2] == "Second note."


def test_the_last_note_does_not_absorb_navigation_or_the_next_section():
    content = (
        'Body.<sup><a href="#fn1" id="cn1">1</a></sup>\n\n### Footnotes\n\n'
        '\\[<a href="#cn1" id="fn1">1</a>\\] Last note.\n\n'
        "[Table of Contents](index.html) \\*\\* [Previous Chapter](x-7.html) (7)\n\n"
        "## Appendix A\n\nUnrelated appendix prose.\n"
    )
    _body, notes = separate_notes(content, "https://example.org/x.html")
    assert [n.text for n in notes] == ["Last note."]
