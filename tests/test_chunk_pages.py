"""Tests for chunk_pages: mapping Chandra chunk markdown to physical PDF pages.

Covers the ``--paginate_output`` separator splitting, physical-page offset math,
output-dir parsing (new page-range dirs and legacy index dirs), the safe
no-separator degrade, and flattened-page detection on a realistic synthetic
task-org block.
"""

from __future__ import annotations

from src.ingestion import chunk_pages as cp

# A minimal flattened 2-D task-org block shaped like the real St. Vith
# "TROOP ASSIGNMENTS" page (group headers with short unit/code tokens beneath).
# Verified to trip page_needs_recovery.
_FLAT = """TROOP ASSIGNMENTS

170300 - March South

CC-A
40
48
A/33

CC-B
31
23
B/33

CC-R
38
17
"""

_PROSE = (
    "This is ordinary narrative prose with no tabular structure whatsoever. "
    "It merely describes the movement of forces in complete sentences."
)


def _sep(zero_based_index: int) -> str:
    """Reproduce Chandra's paginate_output separator for the given page index."""
    return "\n\n" + str(zero_based_index) + "-" * 48 + "\n\n"


def test_chunk_start_page_from_dir_pagerange():
    """Page-range dir names map to their first physical page."""
    assert cp.chunk_start_page_from_dir("chunk-p0151-0200") == 151
    assert cp.chunk_start_page_from_dir("chunk-p0001-0050") == 1
    assert cp.chunk_start_page_from_dir("chunk-p0007") == 7
    # With a full S3-style path prefix.
    assert cp.chunk_start_page_from_dir("ocr-output/book/chunk-p0126-0150/") == 126


def test_chunk_start_page_from_dir_legacy_returns_none():
    """Legacy index-only dirs carry no page info."""
    assert cp.chunk_start_page_from_dir("chunk-003") is None
    assert cp.chunk_start_page_from_dir("chunk-000") is None


def test_split_pages_offsets():
    """Pages map to chunk_start + positional offset."""
    chunk = _PROSE + _sep(1) + _FLAT + _sep(2) + _PROSE
    pages = cp.split_pages(chunk, chunk_start_page=151)
    assert [p for p, _ in pages] == [151, 152, 153]
    # Content routed to the right page.
    assert "TROOP ASSIGNMENTS" in pages[1][1]
    assert "TROOP ASSIGNMENTS" not in pages[0][1]


def test_split_pages_no_separator_is_single_page():
    """No separators degrades to one page at the start."""
    pages = cp.split_pages(_PROSE, chunk_start_page=42)
    assert len(pages) == 1
    assert pages[0][0] == 42


def test_split_pages_start_page_one():
    """Offset math holds when the chunk starts at page 1."""
    chunk = _PROSE + _sep(1) + _PROSE
    pages = cp.split_pages(chunk, chunk_start_page=1)
    assert [p for p, _ in pages] == [1, 2]


def test_flattened_pages_detected_at_correct_physical_page():
    """A flattened block resolves to its physical page."""
    chunk = _PROSE + _sep(1) + _FLAT + _sep(2) + _PROSE
    # chunk starts at physical page 151 → flattened block is the 2nd page → 152.
    assert cp.flattened_pages_in_chunk(chunk, chunk_start_page=151) == [152]


def test_flattened_pages_none_for_all_prose():
    """All-prose chunks route nothing."""
    chunk = _PROSE + _sep(1) + _PROSE
    assert not cp.flattened_pages_in_chunk(chunk, chunk_start_page=10)


def test_flattened_single_page_chunk_no_markers():
    """A single-page flattened chunk still routes."""
    # A single-page chunk with a flattened table and no separators still routes.
    assert cp.flattened_pages_in_chunk(_FLAT, chunk_start_page=155) == [155]


def test_page_separator_does_not_match_content_rules():
    """A plain 48-dash rule is not a page separator."""
    # A markdown horizontal rule / table border must NOT be treated as a page
    # separator (different length / not digit-prefixed).
    not_a_sep = _PROSE + "\n\n" + "-" * 48 + "\n\n" + _FLAT
    pages = cp.split_pages(not_a_sep, chunk_start_page=5)
    assert len(pages) == 1  # no digit-prefixed 48-dash separator → one page
