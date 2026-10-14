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
