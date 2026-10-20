"""TODO #2: parser captures endnote CITATION TEXT (#fn marker paired with #cn def)."""

from src.parser import extract_footnotes

_SRC = """\
Some narrative.<sup>[1](https://ex.org/USA-E-Siegfried-2.html#fn1)</sup> More.<sup>[18](https://ex.org/USA-E-Siegfried-2.html#fn18)</sup>

> ### Footnotes

> [1](https://ex.org/USA-E-Siegfried-2.html#cn1). 12th A Gp and FUSA G-1 Daily Summaries, 12 Sep 44, FUSA G-2 TAC Misc file.

> [18](https://ex.org/USA-E-Siegfried-2.html#cn18). Research and Development Service, Comparison of Ordnance, 6 May 1945.
"""


def test_captures_citation_text_paired_by_number():
    fns = extract_footnotes(_SRC)
    by_num = {n: (u, t) for n, u, t in fns}
    assert by_num[1][1].startswith("12th A Gp and FUSA G-1 Daily Summaries")
    assert by_num[18][1].startswith("Research and Development Service")
    # url comes from the in-text #fn marker
    assert "#fn1" in by_num[1][0]


def test_marker_without_definition_has_empty_text():
    src = "X.<sup>[5](https://ex.org/p.html#fn5)</sup>"
    fns = extract_footnotes(src)
    assert fns == [(5, "https://ex.org/p.html#fn5", "")]


def test_definition_only_footnote_captured():
    src = "> [9](https://ex.org/p.html#cn9). A standalone endnote definition."
    fns = extract_footnotes(src)
    assert fns == [(9, "https://ex.org/p.html#cn9", "A standalone endnote definition.")]


def test_no_footnotes_empty():
    assert extract_footnotes("plain text, no notes") == []


def test_definition_leading_bracket_artifact_stripped():
    """The ibiblio markup wraps the anchor in literal brackets: ``[N] text`` -> after link
    conversion the captured trailing text begins with a stray ``]``. It must be stripped so
    the stored citation text is clean (this was part of the bibliography=0 bug)."""
    src = "[7](https://ex.org/p.html#cn7) ] Real citation text, p. 5."
    fns = extract_footnotes(src)
    assert fns == [(7, "https://ex.org/p.html#cn7", "Real citation text, p. 5.")]


def test_import_to_parse_chain_captures_definition_text():
    """END-TO-END regression for bibliography=0: the ibiblio <h3>Footnotes</h3> block uses
    ``<p>[<a name=fnN href=#cnN>N</a>] citation`` definition anchors. The importer must emit
    the parser-recognized ``[N](url#cnN) citation`` form so Footnote.text is populated (it was
    empty before the fix, so every note was skipped as a stub and the bibliography stored 0).
    """
    import importlib.util
    import sys

    import pytest

    BeautifulSoup = pytest.importorskip("bs4").BeautifulSoup

    spec = importlib.util.spec_from_file_location(
        "imp_hw", "scripts/import_hyperwar_html.py"
    )
    imp = importlib.util.module_from_spec(spec)
    sys.modules["imp_hw"] = imp
    try:
        spec.loader.exec_module(imp)
    except SystemExit:
        pass

    url = "https://ex.org/USA-E-XChannel/USA-E-XChannel-1.html"
    html = (
        "<html><body><p>Narrative text."
        '<a href="USA-E-XChannel-1.html#fn1">1</a></p>'
        "<hr><center><h3>Footnotes</h3></center>"
        '<p>[<a name="fn1" href="#cn1">1</a>] '
        "Administrative History of U.S. Naval Forces, MS, pp. 2ff. Hist Div files."
        "</body></html>"
    )
    md = imp.convert_html_to_markdown(BeautifulSoup(html, "html.parser"), url)
    fns = extract_footnotes(md)
    by_num = {n: (u, t) for n, u, t in fns}
    assert 1 in by_num, f"footnote 1 not captured; md was:\n{md}"
    # text is populated AND clean (no leading ']')
    assert by_num[1][1].startswith("Administrative History of U.S. Naval Forces")
    assert not by_num[1][1].startswith("]")


def test_fetch_endnote_texts_prefers_parsed_text(tmp_path):
    import json
    from src.extraction.fetch_endnotes import fetch_endnote_texts

    p = tmp_path / "chapter2a-parsed.json"
    p.write_text(
        json.dumps(
            {
                "footnotes": [
                    {
                        "number": 1,
                        "url": "https://ex/#fn1",
                        "text": "Daily Summaries, 12 Sep 44.",
                    },
                    {
                        "number": 3,
                        "url": "https://ex/#fn3",
                        "text": "Interv with Maj Gen Thorson.",
                    },
                ]
            }
        )
    )
    # No network: all text present in parsed -> used directly.
    assert fetch_endnote_texts(p) == {
        1: "Daily Summaries, 12 Sep 44.",
        3: "Interv with Maj Gen Thorson.",
    }


def test_fetch_endnote_texts_skips_non_dict_entries(tmp_path):
    """Malformed/legacy parsed footnotes containing a non-dict entry must not crash."""
    import json
    from src.extraction.fetch_endnotes import fetch_endnote_texts

    p = tmp_path / "chapter2a-parsed.json"
    p.write_text(
        json.dumps(
            {
                "footnotes": [
                    5,  # malformed non-dict entry
                    {"number": 1, "url": "https://ex/#fn1", "text": "Real citation."},
                ]
            }
        )
    )
    # Non-dict skipped; the valid parsed-text entry still returned; no crash, no network.
    assert fetch_endnote_texts(p) == {1: "Real citation."}


def test_importer_drops_dangerous_scheme_footnote_url():
    """Security: a crafted javascript:/data: href in untrusted source HTML must NOT be emitted
    as a markdown link; the legitimate footnote is still captured."""
    import importlib.util
    import sys

    import pytest

    BeautifulSoup = pytest.importorskip("bs4").BeautifulSoup
    spec = importlib.util.spec_from_file_location(
        "imp_hw2", "scripts/import_hyperwar_html.py"
    )
    imp = importlib.util.module_from_spec(spec)
    sys.modules["imp_hw2"] = imp
    try:
        spec.loader.exec_module(imp)
    except SystemExit:
        pass
    url = "https://ex.org/USA-E-XChannel/USA-E-XChannel-1.html"
    html = (
        "<html><body><p>Text"
        '<a href="javascript:alert(1)#fn9">9</a></p>'
        "<center><h3>Footnotes</h3></center>"
        '<p>[<a name="fn9" href="#cn9">9</a>] Legit citation text.</body></html>'
    )
    md = imp.convert_html_to_markdown(BeautifulSoup(html, "html.parser"), url)
    assert "javascript:alert(1)" not in md  # dangerous marker url dropped
    from src.parser import extract_footnotes

    by_num = {n: (u, t) for n, u, t in extract_footnotes(md)}
    assert by_num[9][1] == "Legit citation text."  # def-anchor text still captured
