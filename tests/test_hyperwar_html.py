"""HyperWar HTML -> one markdown paragraph per source paragraph (src/ingestion/hyperwar_html).

Fixtures are the COMPLETE source pages (never truncated):
  USA-E-XChannel-8      https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html
  USA-E-Logistics1-3    .../USA/USA-E-Logistics1/USA-E-Logistics1-3.html (tables, captions)
  USA-CBI-Mission-2     .../USA/USA-CBI-Mission/USA-CBI-Mission-2.html (quotations, lists)
  USA-MTO-NWA-4         .../USA/USA-MTO-NWA/USA-MTO-NWA-4.html

Before: a whole printed page was one "paragraph", page markers and rules were paragraphs, a
paragraph crossing a page was cut in two, and three of the four books parsed 0 notes.
"""

from __future__ import annotations

import html as htmlmod
import logging
import re
from collections import Counter
from pathlib import Path

import pytest

from src.ingestion import hyperwar_html as H
from src.ingestion.text_converters import pandoc_available

FIXTURES = Path(__file__).parent / "fixtures" / "hyperwar"
BOOKS = {
    "USA-E-XChannel-8": "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-8.html",
    "USA-E-Logistics1-3": "https://www.ibiblio.org/hyperwar/USA/USA-E-Logistics1/USA-E-Logistics1-3.html",
    "USA-CBI-Mission-2": "https://www.ibiblio.org/hyperwar/USA/USA-CBI-Mission/USA-CBI-Mission-2.html",
    "USA-MTO-NWA-4": "https://www.ibiblio.org/hyperwar/USA/USA-MTO-NWA/USA-MTO-NWA-4.html",
}
# Note definitions in each source page (= its highest note number; every one has its text).
NOTES = {
    "USA-E-XChannel-8": 102,
    "USA-E-Logistics1-3": 116,
    "USA-CBI-Mission-2": 105,
    "USA-MTO-NWA-4": 79,
}
needs_pandoc = pytest.mark.skipif(not pandoc_available(), reason="pandoc not installed")


def _raw(book: str) -> str:
    return (FIXTURES / f"{book}.html").read_text(encoding="utf-8", errors="replace")


def _words(html: str) -> Counter:
    html = re.sub(r"<head\b.*?</head>", " ", html, flags=re.S | re.I)
    html = re.sub(
        r"<i>\s*--\s*[\d/ ]+\s*--\s*</i>", " ", html
    )  # page labels become anchors
    html = re.sub(
        r"</?font\b[^>]*>", "", html
    )  # small caps: B<font>OLERO</font> = BOLERO
    return Counter(re.findall(r"\w+", htmlmod.unescape(re.sub(r"<[^>]*>", " ", html))))


@pytest.fixture(scope="module")
def normalised():
    return {book: H.normalise_hyperwar_html(_raw(book)) for book in BOOKS}


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    """Each book through the real converter + parser."""
    if not pandoc_available():
        pytest.skip("pandoc not installed")
    from src.ingestion.text_converters import html_to_markdown
    from src.models import Metadata
    from src.parser import parse_content_file

    out = {}
    for book, url in BOOKS.items():
        md_file = tmp_path_factory.mktemp(book) / "chapter1-content.md"
        md_file.write_text(
            html_to_markdown(FIXTURES / f"{book}.html"), encoding="utf-8"
        )
        out[book] = parse_content_file(
            md_file, "full", 1, Metadata(book=book, author="A", source_url=url)
        )
    return out


# --- every source word survives, nothing is invented -------------------------------------------


@pytest.mark.parametrize("book", BOOKS)
def test_every_word_is_kept_exactly(book, normalised):
    norm, report = normalised[book]
    assert report.pages > 20
    assert _words(_raw(book)) == _words(norm)


@pytest.mark.parametrize("book", BOOKS)
def test_no_page_frame_label_or_rule_survives(book, normalised):
    norm, _ = normalised[book]
    assert not re.search(r"<i>\s*--\s*\d+", norm)  # page labels
    assert "<hr" not in norm.lower()
    assert "<font" not in norm.lower()


@pytest.mark.parametrize("book", BOOKS)
def test_every_source_page_anchor_is_kept(book, normalised):
    norm, _ = normalised[book]
    source = {int(n) for n in re.findall(r"name\s*=\s*\"?page(\d+)", _raw(book), re.I)}
    kept = {int(n) for n in re.findall(r'<a id="page(\d+)"></a>', norm)}
    assert source <= kept


# --- page-split paragraphs ----------------------------------------------------------------------


def _paragraph_with(norm: str, text: str) -> str:
    found = [p for p in re.findall(r"<p>(.*?)</p>", norm, re.S) if text in p]
    assert len(found) == 1, f"{text!r} is in {len(found)} paragraphs"
    return found[0]


def test_a_paragraph_across_a_page_of_photos_is_one_paragraph(normalised):
    para = _paragraph_with(normalised["USA-E-XChannel-8"][0], "bombs were dropped in")
    assert "a camp near Falmouth" in para
    assert '<a id="page271"></a>' in para and '<a id="page272"></a>' in para
    assert "<img" not in para  # the photos moved after it


def test_a_paragraph_across_a_table_and_a_page_is_one_paragraph(normalised):
    para = _paragraph_with(normalised["USA-E-Logistics1-3"][0], "ETOUSA's stated")
    assert "needs. Once more it gave its tentative approval" in para
    assert "<table" not in para.lower()


def test_a_semicolon_does_not_end_a_paragraph(normalised):
    para = _paragraph_with(
        normalised["USA-E-Logistics1-3"][0], "two vehicles in four cases;"
    )
    assert "Diamond T cargo trucks" in para


def test_a_page_ending_a_sentence_before_a_new_paragraph_is_kept_apart(normalised):
    norm = normalised["USA-E-XChannel-8"][0]
    para = _paragraph_with(norm, 'going to be "chancey."')
    assert "General Eisenhower refused" not in para


# --- tables, captions, quotations, small caps -----------------------------------------------------


def test_a_table_missing_row_ends_keeps_its_rows_and_header(normalised):
    norm = normalised["USA-E-Logistics1-3"][0]
    table = re.search(r"<table.*?</table>", norm, re.S | re.I).group(0)
    assert "<thead>" in table and "Type" in table.split("</thead>")[0]
    assert table.count("<tr") == table.count("</tr>") == 5


def test_captions_and_table_notes_are_figures_not_quotations(normalised):
    norm, report = normalised["USA-E-Logistics1-3"]
    assert report.captions == 5 and report.quotes == 0
    assert "<blockquote>" not in norm
    assert re.search(r"<div>\s*GENERAL DEPOT AT ASHCHURCH", norm)


def test_quoted_documents_stay_quotations(normalised):
    norm, report = normalised["USA-CBI-Mission-2"]
    assert report.quotes == 6
    quotes = re.findall(r"<blockquote>(.*?)</blockquote>", norm, re.S)
    assert any(
        "By direction of the President" in q for q in quotes
    )  # Stilwell's orders
    assert any("Generalissimo Chiang saying" in q for q in quotes)


def test_small_caps_are_one_word(normalised):
    norm = normalised["USA-E-Logistics1-3"][0]
    assert "BOLERO" in norm and "B OLERO" not in norm


# --- notes: one canonical form for every book ---------------------------------------------------------


@pytest.mark.parametrize("book", BOOKS)
def test_every_note_is_parsed_with_its_text_and_citing_paragraph(book, parsed):
    notes = parsed[book].footnotes
    assert len(notes) == NOTES[book]
    assert all(n.text and not n.text.startswith(".") for n in notes)
    assert all(n.paragraph_number for n in notes)


def test_a_note_with_a_quoted_list_keeps_all_of_it(parsed):
    note = next(n for n in parsed["USA-CBI-Mission-2"].footnotes if n.number == 23)
    assert note.text.startswith("The six points of General Brett's plan were:")
    assert "Hope is expressed" in note.text and note.text.endswith(
        "Memo cited n. 21(1)."
    )


# --- through the parser ----------------------------------------------------------------------------------


@pytest.mark.parametrize("book", BOOKS)
def test_parsed_paragraphs_are_real_paragraphs_with_pages(book, parsed):
    paras = parsed[book].paragraphs
    junk = [p for p in paras if re.fullmatch(r"[\s*\\\-\d/]*", p.text)]
    assert junk == []  # no page label or rule is a paragraph
    assert all(len(p.text) < 8000 for p in paras)  # no whole page as one paragraph
    assert sum(1 for p in paras if p.page_number is not None) >= len(paras) - 1
    assert any(p.page_end and p.page_end > p.page_number for p in paras)
    assert parsed[book].page_markers


def test_quotations_reach_the_parser_as_quotes(parsed):
    quotes = [p for p in parsed["USA-CBI-Mission-2"].paragraphs if p.is_quote]
    assert any("By direction of the President" in p.text for p in quotes)
    assert not [p for p in parsed["USA-E-XChannel-8"].paragraphs if p.is_quote]


@pytest.mark.parametrize("book", BOOKS)
def test_images_keep_their_captions_and_anchors(book, parsed):
    images = parsed[book].images
    assert images and all(i.paragraph_number for i in images)
    assert any(i.caption for i in images)


# --- rules, on small pages ---------------------------------------------------------------------------------


def _page(*pages: str, last_anchor: bool = True) -> str:
    """A HyperWar page with the given printed pages (page N+1 starts after page N's footer)."""
    out = ["<html><body><a name=page1></a>\n<blockquote>\n"]
    for n, text in enumerate(pages, start=1):
        out.append(text)
        anchor = f"<a name=page{n + 1}></a>" if (last_anchor or n < len(pages)) else ""
        out.append(
            f"\n</blockquote>\n<p><center><i>--{n}--</i></center><hr>{anchor}<p>\n<blockquote>\n"
        )
    out.append("</blockquote></body></html>")
    return "".join(out)


def _paras(html: str) -> list:
    norm, _ = H.normalise_hyperwar_html(html)
    return [re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<p>(.*?)</p>", norm, re.S)]


@pytest.mark.parametrize(
    "end,start,joined",
    [
        ("the troops moved toward the", "river at dawn.", True),  # mid-sentence
        ("They held the bridge.", "and then withdrew.", True),  # lower-case start
        ("They held the bridge;", "The enemy withdrew.", True),  # ';' never ends one
        ("They held the bridge.", "The next day they withdrew.", False),
        ('He said "go."', "They went.", False),
        ("They held it.<sup><a name=cn4 href=#fn4>4</a></sup>", "Next day.", False),
    ],
)
def test_continuation_rule(end, start, joined):
    paras = _paras(_page(f"First.\n<p>\n{end}", start))
    assert (len(paras) == 2) is joined, paras


def test_bare_p_tags_make_separate_paragraphs():
    assert len(_paras(_page("One.\n<p>\nTwo.\n<p>\nThree.", "Four."))) == 4


def test_a_heading_is_a_boundary():
    page2 = "<center><h3><i>The Assault</i></h3></center>\nwent ashore."
    assert len(_paras(_page("They moved toward the", page2))) == 2


def test_a_page_top_figure_moves_after_the_joined_paragraph():
    page2 = (
        "<center><a href=img/x.jpg><img src=icn/x.jpg></a><br>A PHOTO.</center>\nriver."
    )
    norm, report = H.normalise_hyperwar_html(_page("They moved toward the", page2))
    assert report.joined == 1 and report.figures_moved == 1
    assert norm.index("toward the") < norm.index("river.") < norm.index("<img")


def test_broken_and_unclosed_page_anchors_still_mark_the_page():
    html = (
        _page("Alpha.", "Beta.", "Gamma.")
        .replace("<a name=page2></a>", "<a name=page2>")
        .replace("<a name=page3></a>", "<a name=page3\n")
    )
    norm, _ = H.normalise_hyperwar_html(html)
    assert '<a id="page2"></a>' in norm and '<a id="page3"></a>' in norm
    assert "<a " not in norm.replace('<a id="page', "")  # no stray link wraps the text


def test_a_label_without_an_anchor_starts_the_next_page():
    norm, report = H.normalise_hyperwar_html(
        _page("Alpha.", "Beta.", last_anchor=False)
    )
    assert report.labels_without_anchor == [2]
    assert '<a id="page3"></a>' in norm


def test_two_anchors_in_one_footer_are_one_break():
    html = _page("They moved toward the", "river.").replace(
        "<a name=page2></a>", "<a name=page2></a><a name=page3></a>"
    )
    para = _paras(html)[0]
    assert "toward the" in para and "river." in para


def test_non_hyperwar_html_is_unchanged():
    html = "<html><body><p>Hello</p><p>World</p></body></html>"
    assert H.normalise_hyperwar_html(html) == (html, H.NormaliseReport())


def test_missing_html5lib_fails_loudly(monkeypatch):
    from bs4 import FeatureNotFound

    from src.ingestion import text_converters as TC

    def no_html5lib(*_a, **_k):
        raise FeatureNotFound("html5lib")

    monkeypatch.setattr("bs4.BeautifulSoup", no_html5lib)
    with pytest.raises(TC.ConverterUnavailable, match="html5lib"):
        TC._normalise_hyperwar(_page("Alpha.", "Beta."), "x.html")


@pytest.mark.parametrize(
    "marker,definition",
    [
        (
            "<sup><a name=cn7 href=#fn7>7</a></sup>",
            "[<a name=fn7 href=#cn7>7</a>] Ltr, A to B.",
        ),
        (
            "<sup><a href=#fn7 name=cn7>7</a></sup>",
            "<a name=fn7 href=#cn7>7</a>. Ltr, A to B.",
        ),
        (
            '<a href="#fn7" name=cn7><sup>7</sup></a>',
            '<a href="#cn7" name=fn7><b>7</b></a>. Ltr, A to B.',
        ),
    ],
)
def test_note_forms_become_the_canonical_form(marker, definition):
    html = _page(f"They held it.{marker}", f"<h3>Footnotes</h3>\n<p>{definition}")
    norm, report = H.normalise_hyperwar_html(html)
    assert report.notes == 2
    assert '<sup><a href="#fn7" name="cn7">7</a></sup>' in norm
    assert '[<a href="#cn7" name="fn7">7</a>] Ltr, A to B.' in norm
    assert "[[" not in norm and "]]" not in norm


def test_the_converter_logs_one_observ_line(caplog, tmp_path):
    from src.ingestion import text_converters as TC

    with caplog.at_level(logging.INFO):
        TC._normalise_hyperwar(_page("Alpha.", "Beta."), "x.html")
    assert any(
        "convert.hyperwar file=x.html pages=2" in r.getMessage() for r in caplog.records
    )


@needs_pandoc
def test_html_to_markdown_end_to_end(tmp_path):
    from src.ingestion.text_converters import html_to_markdown

    f = tmp_path / "p.html"
    f.write_text(
        _page("They moved toward the", "river at dawn.\n<p>\nNext."), encoding="utf-8"
    )
    md = html_to_markdown(f)
    assert '<span id="page2"></span>' in md
    assert not re.search(r"^>", md, re.M) and "--1--" not in md
    blocks = [b for b in md.split("\n\n") if b.strip() and "page1" not in b]
    assert any("toward the" in b and "river at dawn." in b for b in blocks)


# --- review hardening -------------------------------------------------------------------------------


def test_an_unclosed_italic_before_a_footer_does_not_swallow_the_book():
    html = _page("They saw <i>Augusta never closed.\n<p>\nSecond.", "Third.", "Fourth.")
    norm, report = H.normalise_hyperwar_html(html)
    assert report.pages == report.labels == 3
    assert not re.search(r"--\s*\d+\s*--", norm)
    assert len(_paras(html)) == 4


def test_a_missing_frame_close_still_splits_the_pages():
    html = _page("Alpha.", "Beta.", "Gamma.").replace(
        "\n</blockquote>\n<p><center><i>--1--", "\n<p><center><i>--1--", 1
    )
    norm, report = H.normalise_hyperwar_html(html)
    assert report.pages == report.labels == 3
    assert "<blockquote>" not in norm  # the unclosed frame is not a quotation
    assert len(_paras(html)) == 3


def test_comments_are_not_turned_into_text_or_markup():
    html = _page("Alpha.<!-- <img src=x onerror=alert(1)> hidden -->", "Beta.")
    norm, _ = H.normalise_hyperwar_html(html)
    assert "hidden" not in norm and "onerror" not in norm and "<img" not in norm


def test_a_page_anchor_with_more_attributes_does_not_leak_text():
    html = _page("They moved toward the", "river.").replace(
        "<a name=page2></a>", "<a name=page2 id=zz title='p 2'></a>"
    )
    norm, _ = H.normalise_hyperwar_html(html)
    assert "id=zz" not in norm and "title=" not in norm
    assert '<a id="page2"></a>' in norm


@pytest.mark.parametrize(
    "end,start",
    [
        ("And so it went\u2026", "The next day."),  # an ellipsis ends a sentence
        (
            "<b>Section Title Without Period</b>",
            "The regiment moved.",
        ),  # run-in heading
        (
            "They held the line.",
            "von Rundstedt ordered a counterattack.",
        ),  # a name particle
    ],
)
def test_real_paragraph_ends_are_kept_apart(end, start):
    assert len(_paras(_page(f"First.\n<p>\n{end}", start))) == 3


def test_the_converter_warns_when_footers_are_not_split(caplog, monkeypatch):
    from src.ingestion import text_converters as TC

    report = H.NormaliseReport(pages=1, labels=3)
    monkeypatch.setattr(H, "normalise_hyperwar_html", lambda raw: ("<p>x</p>", report))
    with caplog.at_level(logging.WARNING):
        TC._normalise_hyperwar("<html></html>", "x.html")
    assert any("convert.hyperwar_unsplit" in r.getMessage() for r in caplog.records)
