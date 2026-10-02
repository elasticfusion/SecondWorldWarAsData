"""Tests for the OCR separator-vs-pagecount integrity check."""

from src.ingestion.separator_check import check_separators


def _page(idx_following: int, text: str) -> str:
    """Build a page separator (0-based index of the FOLLOWING page + 48 dashes)
    then the text, matching chandra --paginate_output."""
    return f"\n\n{idx_following}{'-' * 48}\n\n{text}"


def _chunk(start_texts):
    """Join page texts with proper separators (first page has none)."""
    out = start_texts[0]
    for i, t in enumerate(start_texts[1:], start=1):
        out += _page(i, t)
    return out


def test_exact_match_ok():
    # chunk starting at physical page 1, 3 pages all with content
    md = _chunk(["page one text", "page two text", "page three text"])
    rep = check_separators([("chunk-p0001-0003", md)], pdf_pages=3)
    assert rep.represented_pages == 3
    assert rep.ok is True
    assert rep.empty_pages == []


def test_missing_separator_undercounts():
    # markdown only represents 2 pages but the PDF has 3 -> mismatch
    md = _chunk(["page one", "page two"])
    rep = check_separators([("chunk-p0001-0003", md)], pdf_pages=3)
    assert rep.represented_pages == 2
    assert rep.ok is False


def test_empty_page_flagged():
    # middle page has no content (blank scan) -> flagged by physical page number
    md = _chunk(["real content", "   ", "more content"])
    rep = check_separators([("chunk-p0001-0003", md)], pdf_pages=3)
    assert 2 in rep.empty_pages
    assert rep.ok is False


def test_physical_page_offset_from_chunk_dir():
    # chunk starts at physical page 51 -> empty 2nd page is physical 52
    md = _chunk(["content", ""])
    rep = check_separators([("chunk-p0051-0052", md)], pdf_pages=2)
    assert rep.empty_pages == [52]


def test_legacy_chunk_reported():
    rep = check_separators([("chunk-003", "whatever")], pdf_pages=1)
    assert rep.legacy_chunks == ["chunk-003"]
    assert rep.ok is False
    assert rep.represented_pages == 0


def test_multi_chunk_counts_distinct_pages():
    c1 = _chunk(["a", "b"])  # pages 1,2
    c2 = _chunk(["c", "d"])  # pages 3,4
    rep = check_separators(
        [("chunk-p0001-0002", c1), ("chunk-p0003-0004", c2)], pdf_pages=4
    )
    assert rep.represented_pages == 4
    assert rep.ok is True
    assert rep.per_chunk == {"chunk-p0001-0002": 2, "chunk-p0003-0004": 2}
