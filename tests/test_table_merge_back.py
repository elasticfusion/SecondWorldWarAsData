"""Tests for merging PP-StructureV3 recovered tables back into markdown."""

from bs4 import BeautifulSoup

from src.ingestion.markdown_structure import detect_flattened_tables
from src.ingestion.table_merge_back import (
    MergeRegion,
    merge_recovered_tables,
    merge_recovery_json,
)

# A flattened task-org block in the shape Chandra emits (no <table> markup),
# surrounded by other content so we can prove the splice is localized.
FLATTENED_MD = """\
COMMAND AND STAFF

<table><tr><td>Comdg Gen</td><td>Brig Gen A B Smith</td></tr></table>

TROOP ASSIGNMENTS

170300 - March South

CC-A
40
48

CC-B
31
23

DIV ARTY
440
434

COMMAND POSTS
"""

RECOVERED_HTML = (
    "<table><tr><td>170300</td><td>CC-A</td><td>CC-B</td><td>DIV ARTY</td></tr>"
    "<tr><td></td><td>40</td><td>31</td><td>440</td></tr></table>"
)


def _flattened_region(md: str, html: str) -> MergeRegion:
    span = detect_flattened_tables(md).spans[0]
    return MergeRegion(span.start_para, span.end_para, html)


def test_detector_finds_the_flattened_block() -> None:
    spans = detect_flattened_tables(FLATTENED_MD).spans
    assert spans, "expected the flattened task-org block to be detected"


def test_merge_replaces_flattened_region_with_table() -> None:
    region = _flattened_region(FLATTENED_MD, RECOVERED_HTML)
    result = merge_recovered_tables(FLATTENED_MD, [region])
    assert result.changed and result.regions_merged == 1
    # The recovered <table> is now present and parseable as HTML.
    soup = BeautifulSoup(result.markdown, "html.parser")
    tables = soup.find_all("table")
    # Original command-staff table + the newly spliced recovered table.
    assert len(tables) == 2
    assert "170300" in result.markdown
    assert "recovered by PP-StructureV3" in result.markdown


def test_merge_preserves_surrounding_content() -> None:
    region = _flattened_region(FLATTENED_MD, RECOVERED_HTML)
    result = merge_recovered_tables(FLATTENED_MD, [region])
    # Sections before and after the flattened block are untouched.
    assert "COMMAND AND STAFF" in result.markdown
    assert "COMMAND POSTS" in result.markdown
    assert "Brig Gen A B Smith" in result.markdown


def test_keep_original_preserves_flattened_text() -> None:
    region = _flattened_region(FLATTENED_MD, RECOVERED_HTML)
    result = merge_recovered_tables(FLATTENED_MD, [region], keep_original=True)
    # The raw flattened lines are retained in a provenance comment.
    assert "original flattened OCR" in result.markdown
    assert "CC-A" in result.markdown  # from the preserved original


def test_no_keep_original_drops_flattened_text() -> None:
    region = _flattened_region(FLATTENED_MD, RECOVERED_HTML)
    result = merge_recovered_tables(FLATTENED_MD, [region], keep_original=False)
    assert "original flattened OCR" not in result.markdown


def test_no_regions_is_noop() -> None:
    result = merge_recovered_tables(FLATTENED_MD, [])
    assert not result.changed
    assert result.markdown == FLATTENED_MD


def test_empty_recovered_html_is_skipped() -> None:
    region = MergeRegion(5, 10, "   ")
    result = merge_recovered_tables(FLATTENED_MD, [region])
    assert not result.changed


def test_merge_from_recovery_json_shape() -> None:
    span = detect_flattened_tables(FLATTENED_MD).spans[0]
    recovery = {
        "flattened_hints": [{"start_line": span.start_para, "end_line": span.end_para}],
        "recovered": {"html": RECOVERED_HTML, "table_count": 1},
    }
    result = merge_recovery_json(FLATTENED_MD, recovery)
    assert result.changed
    assert "170300" in result.markdown


def test_recovery_json_no_html_is_noop() -> None:
    recovery = {
        "flattened_hints": [{"start_line": 5, "end_line": 10}],
        "recovered": {"html": "", "table_count": 0},
    }
    result = merge_recovery_json(FLATTENED_MD, recovery)
    assert not result.changed
