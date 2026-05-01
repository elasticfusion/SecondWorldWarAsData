"""Tests for src/extraction/fetch_endnotes.py."""

import json
from pathlib import Path
from unittest.mock import patch

from src.extraction.fetch_endnotes import (
    _group_by_page,
    _parse_footnotes_from_html,
    _resolve_cross_references,
    fetch_endnote_texts,
    format_endnote_text_block,
)

# --- HTML fixtures ---

BREAKOUT_HTML = """<html><body>
<p><a name="fn1"></a>1.
Dir, CCS to SCAEF, 12 Feb 44, quoted in Harrison, Cross-Channel Attack.</p>
<p><a name="fn2"></a>2.
COSSAC (43) 28, Opn OVERLORD, 15 Jul 43.</p>
<p><a name="fn3"></a>3.
Ruppenthal, Logistical Support, I, 421.</p>
</body></html>"""

XCHANNEL_HTML = """<html><body>
<p>[<a name=fn1 href=#cn1>1</a>]
Administrative History of U.S. Naval Forces in Europe, 1940-1946, MS, pp. 2ff.</p>
<p>[<a name=fn2 href=#cn2>2</a>]
See n. 1.</p>
</body></html>"""

LORRAINE_HTML = """<html><body>
<p align=justify><a href="#cn1" name=fn1><b>1</b></a>. The Third Army official Diary (MS).</p>
<p align=justify><a href="#cn2" name=fn2><b>2</b></a>. Penned Msg, cited n. 1, above.</p>
</body></html>"""


class TestParseFootnotesFromHtml:
    """Test HTML parsing for all three book patterns."""

    def test_breakout_pattern(self):
        fns = _parse_footnotes_from_html(BREAKOUT_HTML)
        assert len(fns) == 3
        assert "Dir, CCS to SCAEF" in fns[1]
        assert "COSSAC (43) 28" in fns[2]
        assert "Ruppenthal" in fns[3]

    def test_xchannel_pattern(self):
        fns = _parse_footnotes_from_html(XCHANNEL_HTML)
        assert len(fns) == 2
        assert "Administrative History" in fns[1]

    def test_lorraine_pattern(self):
        fns = _parse_footnotes_from_html(LORRAINE_HTML)
        assert len(fns) == 2
        assert "Third Army official Diary" in fns[1]

    def test_empty_html(self):
        assert _parse_footnotes_from_html("<html></html>") == {}


class TestResolveCrossReferences:
    """Test cross-reference resolution for all patterns."""

    def test_cited_in_n(self):
        fns = {2: "COSSAC plan.", 7: "NEPTUNE cited in n. 2, above."}
        resolved = _resolve_cross_references(fns)
        assert "COSSAC plan." in resolved[7]

    def test_cited_n_without_in(self):
        fns = {24: "Penned Msg, Bradley.", 31: "Penned Msg, cited n. 24, above."}
        resolved = _resolve_cross_references(fns)
        assert "Penned Msg, Bradley." in resolved[31]

    def test_see_n(self):
        fns = {4: "Brief of ABC-I Conv.", 19: "1 Brief. See n. 4."}
        resolved = _resolve_cross_references(fns)
        assert "Brief of ABC-I Conv." in resolved[19]

    def test_no_self_reference(self):
        fns = {5: "See n. 5 for details."}
        resolved = _resolve_cross_references(fns)
        assert resolved[5] == "See n. 5 for details."

    def test_missing_target(self):
        fns = {7: "See n. 99."}
        resolved = _resolve_cross_references(fns)
        assert resolved[7] == "See n. 99."

    def test_no_cross_references(self):
        fns = {1: "Simple citation.", 2: "Another citation."}
        resolved = _resolve_cross_references(fns)
        assert resolved == fns


class TestGroupByPage:
    """Test footnote grouping by base URL."""

    def test_groups_by_base_url(self):
        meta = [
            {"number": 1, "url": "https://example.com/fn1.html#fn1"},
            {"number": 2, "url": "https://example.com/fn1.html#fn2"},
            {"number": 3, "url": "https://example.com/fn2.html#fn3"},
        ]
        groups = _group_by_page(meta)
        assert groups["https://example.com/fn1.html"] == [1, 2]
        assert groups["https://example.com/fn2.html"] == [3]

    def test_empty_input(self):
        assert _group_by_page([]) == {}

    def test_missing_url(self):
        assert _group_by_page([{"number": 1}]) == {}


class TestFormatEndnoteTextBlock:
    """Test prompt text block formatting."""

    def test_formats_available_texts(self):
        texts = {1: "Citation one.", 3: "Citation three."}
        block = format_endnote_text_block(texts, [1, 2, 3])
        assert "1. Citation one." in block
        assert "3. Citation three." in block
        assert "2." not in block

    def test_empty_when_no_matches(self):
        assert format_endnote_text_block({1: "text"}, [99]) == ""

    def test_empty_when_no_texts(self):
        assert format_endnote_text_block({}, [1, 2]) == ""

    def test_header_present(self):
        block = format_endnote_text_block({1: "text"}, [1])
        assert block.startswith("Actual endnote/footnote text")


class TestFetchEndnoteTexts:
    """Test the main fetch function with mocked HTTP."""

    def test_fetches_and_parses(self, tmp_path):
        parsed = tmp_path / "chapter1-parsed.json"
        parsed.write_text(
            json.dumps(
                {
                    "footnotes": [
                        {"number": 1, "url": "https://example.com/fn1.html#fn1"},
                        {"number": 2, "url": "https://example.com/fn1.html#fn2"},
                    ]
                }
            )
        )

        with patch(
            "src.extraction.fetch_endnotes._fetch_page", return_value=BREAKOUT_HTML
        ):
            texts = fetch_endnote_texts(parsed)

        assert 1 in texts
        assert 2 in texts
        assert "Dir, CCS to SCAEF" in texts[1]

    def test_no_footnotes(self, tmp_path):
        parsed = tmp_path / "chapter1-parsed.json"
        parsed.write_text(json.dumps({"footnotes": []}))
        assert fetch_endnote_texts(parsed) == {}

    def test_missing_file(self, tmp_path):
        assert fetch_endnote_texts(tmp_path / "missing.json") == {}
