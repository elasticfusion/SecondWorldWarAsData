"""Tests for the block-quote detector (Chandra markdown structural repair).

Grounded in the St. Vith / Boyer OCR probe: Chandra emits quotations as ordinary
paragraphs with no ``>`` markup (the P12 failure). The detector must re-mark a
clearly-introduced quotation, extract its attribution/page-cite, and NOT misfire
on footnotes, tables, or ordinary quoted phrases mid-prose.
"""

from src.ingestion.markdown_structure import (
    detect_block_quotes,
    detect_flattened_tables,
)

# The real P12 shape: an explicit lead-in, then multi-paragraph quotation where
# each paragraph re-opens the double quote.
P12 = (
    "## INTRODUCTION\n\n"
    "ST. VITH, BELGIUM, is a name which always will kindle memories ...\n\n"
    "It was an epic stand — one which will not be forgotten ...\n\n"
    "In the words of Mr. Ralph Ingersoll's controversial book, "
    "Top Secret: (pp. 270-271)\n\n"
    '"Bastogne and St. Vith were the road centers the Germans had to have '
    "to support any drive strong enough to carry them across the Meuse.\n\n"
    '"What newspapers did not tell was what happened back of St. Vith."\n\n'
    "This Narrative After Action Report is an attempt to recount ..."
)


def test_detects_leadin_quotation_and_remarks() -> None:
    result = detect_block_quotes(P12)
    assert result.changed is True
    assert len(result.spans) == 1
    span = result.spans[0]
    assert span.needs_review is False
    assert span.confidence >= 0.9
    assert span.page_cite == "pp. 270-271"
    assert span.attribution and "Ingersoll" in span.attribution
    # Both quoted paragraphs are re-marked; prose around them is not.
    assert '> "Bastogne and St. Vith' in result.markdown
    assert '> "What newspapers did not tell' in result.markdown
    assert result.markdown.count("\n> ") >= 2
    # The lead-in and surrounding prose remain unquoted.
    assert "\n> In the words of" not in result.markdown
    assert "\n> This Narrative" not in result.markdown


def test_quotation_words_are_unchanged() -> None:
    # Verification-flagging discipline: only "> " is added, never the words.
    result = detect_block_quotes(P12)
    for line in result.markdown.splitlines():
        if line.startswith("> "):
            assert line[2:] in P12 or line[2:] == ""  # exact quoted text preserved


def test_footnotes_are_not_misdetected() -> None:
    md = (
        "The Division moved out at 1730. <sup>2</sup>\n\n"
        "---\n\n"
        "<sup>2</sup> Report by the Supreme Commander ... p. 75.\n\n"
        '<sup>3</sup> After Action Report ... "The Battle of St. Vith", p. 1.'
    )
    result = detect_block_quotes(md)
    assert result.changed is False
    assert result.spans == []


def test_table_and_headings_ignored() -> None:
    md = (
        "## STATISTICS\n\n"
        '(a) As reported at time "After Action Reports" were prepared:\n\n'
        "<table><tbody><tr><td>Killed</td><td>43</td></tr></tbody></table>"
    )
    result = detect_block_quotes(md)
    assert result.changed is False


def test_weak_quote_block_flagged_not_applied() -> None:
    # A quote-shaped block with no lead-in is detected but left untouched.
    md = (
        "Some ordinary introductory sentence.\n\n"
        '"A standalone quoted passage that opens and closes with quotes."\n\n'
        "Following prose."
    )
    result = detect_block_quotes(md)
    assert len(result.spans) == 1
    assert result.spans[0].needs_review is True
    # Weak detection is NOT auto-applied.
    assert result.changed is False
    assert "> " not in result.markdown


def test_empty_and_plain_prose_noop() -> None:
    assert detect_block_quotes("").spans == []
    plain = 'Just a paragraph.\n\nAnother paragraph with a "quoted" word inside.'
    result = detect_block_quotes(plain)
    assert result.changed is False
    assert result.spans == []


# --- flattened task-org / 2-D table detection ----------------------------

# The real P155 shape: time-snapshot headers, each followed by command-group
# headers (CC-A, CC-B, ...) with unit-token lines. Chandra flattens the 2-D grid
# to this linear list with no <table> markup. Includes a numeric unit token
# ("3967" under DIV TMS) that must NOT be mistaken for a time snapshot.
P155 = (
    "PROOF ASSIGNMENTS\n\n"
    "170300 - March South\n\n"
    "CC-A\n40\n48\nA/33\n\n"
    "CC-B\n31\n23\nB/33\n\n"
    "DIV TMS\n77\n129\n446\n3967\nB/203\n\n"
    "172400\n\n"
    "CC-A\n40\n48\nA/33\n\n"
    "CC-B\n87\n31\nB/33\n\n"
    "DIV TMS\n77\n129\n446\n3967\nB/203"
)


def test_detects_flattened_task_org_table() -> None:
    result = detect_flattened_tables(P155)
    # Never rewrites — flag only.
    assert result.changed is False
    assert len(result.spans) >= 1
    span = result.spans[0]
    assert span.needs_review is True
    # Snapshot label and descriptor extracted.
    labels = [s.label for s in span.snapshots]
    assert "170300" in labels
    first = next(s for s in span.snapshots if s.label == "170300")
    assert first.descriptor == "March South"
    groups = {g.group for s in span.snapshots for g in s.groups}
    assert any(g.startswith("CC-A") for g in groups)
    assert any(g.startswith("CC-B") for g in groups)


def test_numeric_unit_not_misread_as_snapshot() -> None:
    # "3967" is a unit under DIV TMS, not a time column.
    result = detect_flattened_tables(P155)
    labels = {s.label for span in result.spans for s in span.snapshots}
    assert "3967" not in labels
    div_tms_units = [
        u
        for span in result.spans
        for s in span.snapshots
        for g in s.groups
        if g.group.upper().startswith("DIV TMS")
        for u in g.units
    ]
    assert "3967" in div_tms_units


def test_prose_not_detected_as_table() -> None:
    assert detect_flattened_tables(P12).spans == []


def test_footnotes_not_detected_as_table() -> None:
    md = (
        "The Division moved out. <sup>2</sup>\n\n"
        "---\n\n"
        "<sup>2</sup> Report by the Supreme Commander, p. 75."
    )
    assert detect_flattened_tables(md).spans == []


def test_single_group_not_enough() -> None:
    # One lone group header is not a task-org table.
    md = "Some heading\n\nCC-A\n40\n48"
    assert detect_flattened_tables(md).spans == []


def test_empty_noop_table() -> None:
    assert detect_flattened_tables("").spans == []


# --- functional integration: detector output through the REAL parser --------


def test_blockquote_survives_real_parser_roundtrip() -> None:
    """End-to-end: re-marked quotations survive src/parser.py as is_quote=True.

    Guards the seam that unit tests missed: the parser previously stripped '>'
    markers, flattening quotations into ordinary prose. The Paragraph model +
    split_into_blocks now preserve the quotation distinction.
    """
    from src.parser import split_into_blocks, split_into_paragraphs

    remarked = detect_block_quotes(P12).markdown
    blocks = split_into_blocks(remarked)
    quote_blocks = [text for text, is_quote in blocks if is_quote]
    assert len(quote_blocks) == 2
    assert any("Bastogne and St. Vith" in t for t in quote_blocks)
    assert any("What newspapers did not tell" in t for t in quote_blocks)
    # The lead-in and body prose are NOT quotes.
    non_quotes = [text for text, is_quote in blocks if not is_quote]
    assert any("In the words of" in t for t in non_quotes)
    # Backward compatibility: split_into_paragraphs still returns plain strings.
    paras = split_into_paragraphs(remarked)
    assert all(isinstance(p, str) for p in paras)
    assert len(paras) == len(blocks)


def test_unmarked_prose_has_no_quote_blocks() -> None:
    """Plain Chandra prose (no block quotes) yields no quote-flagged blocks."""
    from src.parser import split_into_blocks

    plain = "## Heading\n\nOrdinary paragraph one.\n\nOrdinary paragraph two."
    assert all(not is_quote for _text, is_quote in split_into_blocks(plain))


# --- functional: full parse_content_file path with structure repair (opt-in) --


def test_parse_content_file_repair_captures_quote_and_attribution(tmp_path) -> None:
    """parse_content_file(apply_structure_repair=True) yields is_quote paragraphs
    carrying quote_attribution; OFF leaves behavior unchanged."""
    from src.models import Metadata
    from src.parser import parse_content_file

    src = tmp_path / "intro.md"
    src.write_text(P12, encoding="utf-8")
    meta = Metadata(
        book="St Vith",
        chapter_title="Introduction",
        author="Boyer",
        series="",
        license="",
    )

    doc = parse_content_file(
        src,
        section_id="a",
        start_paragraph_num=1,
        metadata=meta,
        apply_structure_repair=True,
    )
    quotes = [p for p in doc.paragraphs if p.is_quote]
    assert len(quotes) == 2
    assert all(
        p.quote_attribution and "Ingersoll" in p.quote_attribution for p in quotes
    )

    # Repair OFF: no quotations flagged (backward compatible default).
    doc_off = parse_content_file(
        src,
        section_id="a",
        start_paragraph_num=1,
        metadata=meta,
        apply_structure_repair=False,
    )
    assert not any(p.is_quote for p in doc_off.paragraphs)


# --- functional: table hints wired through parse_content_file ---------------


def test_parse_content_file_populates_table_hints(tmp_path) -> None:
    """parse_content_file(apply_structure_repair=True) attaches flattened-table
    hints (review-flagged) to the document; OFF leaves table_hints empty."""
    from src.models import Metadata
    from src.parser import parse_content_file

    src = tmp_path / "assignments.md"
    src.write_text(P155, encoding="utf-8")
    meta = Metadata(
        book="St Vith",
        chapter_title="Assignments",
        author="Boyer",
        series="",
        license="",
    )

    doc = parse_content_file(
        src,
        section_id="a",
        start_paragraph_num=1,
        metadata=meta,
        apply_structure_repair=True,
    )
    assert len(doc.table_hints) >= 1
    hint = doc.table_hints[0]
    assert hint["needs_review"] is True
    labels = [s["label"] for s in hint["snapshots"]]
    assert "170300" in labels
    groups = {g["group"] for s in hint["snapshots"] for g in s["groups"]}
    assert any(gr.startswith("CC-A") for gr in groups)

    doc_off = parse_content_file(
        src,
        section_id="a",
        start_paragraph_num=1,
        metadata=meta,
        apply_structure_repair=False,
    )
    assert doc_off.table_hints == []
