"""Phase-1 recommended fixes (P1-B/P1-C/Roman/observability) — one test per fix.

Each fix below gets a caplog/behavior test (hard requirement). P1-A (ReDoS) is
covered separately by tests/test_parser_redos.py and is NOT re-tested here.
"""

import json
import logging
import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


# ---------------------------------------------------------------------------
# P1-B: Lambda path-traversal containment + book_name validation
# ---------------------------------------------------------------------------
def test_p1b_path_traversal_key_is_contained(tmp_path, caplog):
    """A '..'-containing S3 key must never write outside tmpdir; it is skipped + logged."""
    import lambda_handlers.phase1_handler as H

    # Sentinel outside the tmp sandbox that must NOT be created.
    outside = tmp_path / "escape_target.txt"
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    storage = Mock()
    # A full key that escapes the sandbox via '..'.
    evil_key = "../escape_target.txt"
    good_key = "contentrepository/Book/chapter1/chapter1-content.md"
    storage.list_files.return_value = [evil_key, good_key]
    storage.read_bytes.return_value = b"data"

    with caplog.at_level(logging.WARNING, logger=H.__name__):
        H._download_book(storage, "Book", sandbox)

    assert "OBSERV phase1.path_traversal_blocked" in caplog.text
    # The traversal target was never written.
    assert not outside.exists()
    # read_bytes was only called for the safe key (evil one skipped before read).
    storage.read_bytes.assert_called_once_with(good_key)
    # The safe file landed inside the sandbox.
    assert (sandbox / good_key).exists()


def test_p1b_bad_book_name_falls_back_and_logs(caplog):
    """A book_name outside the safe charset -> safe fallback + loud OBSERV."""
    import lambda_handlers.phase1_handler as H

    with caplog.at_level(logging.WARNING, logger=H.__name__):
        name = H._safe_book_name("content/../../etc/passwd/x")
    assert name == "_unknown_book"
    assert "OBSERV phase1.book_rejected" in caplog.text
    assert "reason=invalid_charset" in caplog.text


def test_p1b_short_key_guards_indexerror(caplog):
    """A key with fewer than 2 segments must not raise IndexError."""
    import lambda_handlers.phase1_handler as H

    with caplog.at_level(logging.WARNING, logger=H.__name__):
        name = H._safe_book_name("content")  # single segment
    assert name == "_unknown_book"
    assert "reason=too_few_segments" in caplog.text


def test_p1b_valid_book_name_passes():
    import lambda_handlers.phase1_handler as H

    assert H._safe_book_name("content/Breakout and Pursuit/chapter1/x.md") == (
        "Breakout and Pursuit"
    )


# ---------------------------------------------------------------------------
# P1-C(a): non-dict YAML metadata guard
# ---------------------------------------------------------------------------
def test_p1c_non_dict_yaml_returns_empty_metadata(tmp_path, caplog):
    """A scalar/list/None YAML meta doc -> empty Metadata (not AttributeError) + loud log."""
    from src.parser import _parse_yaml_metadata
    from src.models import Metadata

    meta = tmp_path / "x-meta.yaml"
    meta.write_text("just a bare string\n", encoding="utf-8")  # scalar, not a mapping

    with caplog.at_level(logging.WARNING, logger="src.parser"):
        md = _parse_yaml_metadata(meta)

    assert isinstance(md, Metadata)
    assert md.book == ""  # empty, no crash
    assert "OBSERV parser.meta_not_mapping" in caplog.text


def test_p1c_list_yaml_also_safe(tmp_path):
    from src.parser import _parse_yaml_metadata
    from src.models import Metadata

    meta = tmp_path / "y-meta.yaml"
    meta.write_text("- a\n- b\n", encoding="utf-8")  # YAML list
    assert isinstance(_parse_yaml_metadata(meta), Metadata)


# ---------------------------------------------------------------------------
# P1-C(b): per-chapter try/except in phase1_parse.main() — one bad chapter
# must not abort the remaining chapters.
# ---------------------------------------------------------------------------
def test_p1c_chapter_error_is_logged_counted_and_continues(
    tmp_path, caplog, monkeypatch
):
    import phase1_parse as P1
    from src.models import ChapterGroup

    # Two chapters: the first raises, the second must still parse.
    good_meta = tmp_path / "chapter2-meta.yaml"
    good_meta.write_text("book: B\n", encoding="utf-8")
    ch_bad = ChapterGroup(
        book="B", chapter_number=1, meta_file=tmp_path / "m1", content_files={}
    )
    ch_good = ChapterGroup(
        book="B", chapter_number=2, meta_file=good_meta, content_files={}
    )

    monkeypatch.setattr(
        P1, "discover_content_structure", lambda _root: {"B": [ch_bad, ch_good]}
    )

    calls = []

    def fake_parse(cg, apply_structure_repair=False):
        calls.append(cg.chapter_number)
        if cg.chapter_number == 1:
            raise ValueError("boom")
        return []  # good chapter parses to nothing (still "parsed")

    monkeypatch.setattr(P1, "parse_chapter", fake_parse)

    # Stub config/paths/logging + referential integrity so main() runs in tmp.
    monkeypatch.setattr(
        P1, "load_config", lambda *a, **k: {"logging": {}, "ingestion": {}}
    )
    monkeypatch.setattr(
        P1,
        "get_paths",
        lambda cfg, base: {
            "content_root": tmp_path / "content",
            "output_root": tmp_path / "output",
        },
    )
    (tmp_path / "content").mkdir()
    monkeypatch.setattr(P1, "setup_logging", lambda **k: logging.getLogger(P1.__name__))

    import src.utils.config as C

    monkeypatch.setattr(
        C, "get_content_root", lambda paths: tmp_path / "output" / "content"
    )
    import src.utils.referential_integrity as RI

    monkeypatch.setattr(RI, "run_for_phase", lambda *a, **k: None)

    with caplog.at_level(logging.INFO, logger=P1.__name__):
        P1.main()

    # Both chapters were attempted (loop did not abort on the first).
    assert calls == [1, 2]
    assert "OBSERV phase1.chapter_error" in caplog.text
    assert "reason=ValueError" in caplog.text
    assert "chapter_errors=1" in caplog.text


# ---------------------------------------------------------------------------
# ROMAN numeral -> int real conversion (data-loss fix).
# ---------------------------------------------------------------------------
def test_roman_distinct_no_collision():
    from src.discovery import _convert_chapter_number

    got = {r: _convert_chapter_number(r) for r in ["II", "III", "IV", "IX", "XIV"]}
    assert got == {"II": 2, "III": 3, "IV": 4, "IX": 9, "XIV": 14}
    # No collision: five inputs -> five distinct ids.
    assert len(set(got.values())) == 5


def test_roman_lowercase_and_arabic():
    from src.discovery import _convert_chapter_number

    assert _convert_chapter_number("iv") == 4
    assert _convert_chapter_number("7") == 7
    assert _convert_chapter_number(5) == 5


def test_roman_garbage_loud_and_non_colliding(caplog):
    from src.discovery import _convert_chapter_number

    with caplog.at_level(logging.WARNING, logger="src.discovery"):
        a = _convert_chapter_number("ZZZ-garbage")
        b = _convert_chapter_number("other-garbage")
    assert "OBSERV discovery.chapter_number_unresolved" in caplog.text
    # Never the old constant 0, and distinct tokens never collide.
    assert a != 0 and b != 0
    assert a != b


# ---------------------------------------------------------------------------
# OBSERV (2): discovery.chapter_skipped on silent return-None paths.
# ---------------------------------------------------------------------------
def test_discovery_chapter_skipped_no_meta(tmp_path, caplog):
    from src.discovery import _process_chapter_dir

    chdir = tmp_path / "chapter1"
    chdir.mkdir()
    (chdir / "chapter1-content.md").write_text(
        "x", encoding="utf-8"
    )  # content but no meta
    with caplog.at_level(logging.WARNING, logger="src.discovery"):
        res = _process_chapter_dir(chdir, "Book")
    assert res is None
    assert "OBSERV discovery.chapter_skipped reason=no_meta" in caplog.text


def test_discovery_chapter_skipped_no_content(tmp_path, caplog):
    from src.discovery import _process_chapter_dir

    chdir = tmp_path / "chapter1"
    chdir.mkdir()
    (chdir / "chapter1-meta.yaml").write_text(
        "book: B\n", encoding="utf-8"
    )  # meta, no content
    with caplog.at_level(logging.WARNING, logger="src.discovery"):
        res = _process_chapter_dir(chdir, "Book")
    assert res is None
    assert "OBSERV discovery.chapter_skipped reason=no_content" in caplog.text


def test_discovery_chapter_skipped_no_chapter_num(tmp_path, caplog):
    from src.discovery import _process_chapter_dir

    chdir = tmp_path / "appendix"  # no chapterN prefix
    chdir.mkdir()
    with caplog.at_level(logging.WARNING, logger="src.discovery"):
        res = _process_chapter_dir(chdir, "Book")
    assert res is None
    assert "OBSERV discovery.chapter_skipped reason=no_chapter_num" in caplog.text


# ---------------------------------------------------------------------------
# OBSERV (1): phase1.chapter_empty for a written chapter with 0 paragraphs.
# ---------------------------------------------------------------------------
def test_phase1_chapter_empty_observed(tmp_path, caplog):
    import phase1_parse as P1
    from src.models import MarkdownDocument

    doc = MarkdownDocument(
        book="Book",
        chapter_number=3,
        chapter_title="Plate 1",
        section_id="",
        author="",
        series="",
        license="Public Domain",
        paragraphs=[],  # empty -> chapter_empty
    )
    book_output = tmp_path / "Book"
    book_output.mkdir()
    with caplog.at_level(logging.WARNING, logger=P1.__name__):
        P1._process_document(doc, book_output, logging.getLogger(P1.__name__))
    assert "OBSERV phase1.chapter_empty book=Book chapter=3" in caplog.text


# ---------------------------------------------------------------------------
# OBSERV (3): guard_parsed_content swallow made loud (still returns True).
# ---------------------------------------------------------------------------
def test_guard_parsed_content_swallow_is_loud(caplog, monkeypatch):
    import src.utils.file_lock as fl

    def _boom(*a, **k):
        raise RuntimeError("validator exploded")

    # Force the outer swallow by exploding inside the try.
    monkeypatch.setattr(fl, "_record_stat", _boom)

    valid = {
        "book": "X",
        "chapter_title": "C",
        "paragraphs": [{"absolute_number": 1, "text": "ok"}],
    }
    with caplog.at_level(logging.WARNING, logger="src.utils.file_lock"):
        out = fl.guard_parsed_content("x-parsed.json", valid)
    assert out is True  # behavior unchanged — still lets the parse through
    assert "guard_parsed_content swallowed validation error" in caplog.text


# ---------------------------------------------------------------------------
# OBSERV (4): Lambda path emits phase1.parsed / phase1.skipped + 'no chapters'.
# ---------------------------------------------------------------------------
def test_lambda_run_parse_no_chapters_is_loud(tmp_path, caplog, monkeypatch):
    import lambda_handlers.phase1_handler as H
    import src.discovery as D

    monkeypatch.setattr(D, "discover_content_structure", lambda root: {})
    # phase1_handler imports discover_content_structure inside _run_parse via src.discovery
    (tmp_path / "content").mkdir()
    storage = Mock()
    with caplog.at_level(logging.WARNING, logger=H.__name__):
        H._run_parse(tmp_path, "MissingBook", storage)
    assert "OBSERV phase1.skipped book=MissingBook reason=no_chapters" in caplog.text


# ---------------------------------------------------------------------------
# OBSERV (5): source_section corrupt-record read is loud (duplicate-ULID risk).
# ---------------------------------------------------------------------------
def test_source_section_corrupt_record_read_is_loud(tmp_path, caplog):
    from src.extraction.source_section import _find_section_record_by_event_id

    section_dir = tmp_path / "source_section"
    section_dir.mkdir()
    (section_dir / "corrupt.json").write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="src.extraction.source_section"):
        res = _find_section_record_by_event_id(tmp_path, "EVT123")
    assert res is None
    assert "OBSERV source_section.record_read_failed" in caplog.text
    assert "duplicate-ULID risk" in caplog.text


# ---------------------------------------------------------------------------
# OBSERV (6): _save_split_chapter rollup chunks_written / chunks_dropped.
# ---------------------------------------------------------------------------
def test_phase1_split_rollup_observed(tmp_path, caplog):
    import phase1_parse as P1
    from src.models import MarkdownDocument, Paragraph

    # Build a doc large enough to trip the 400k-char split threshold.
    big = "word " * 2000  # ~10k chars
    paras = [
        Paragraph(
            absolute_number=i, text=big, page_number=i, section_id="", source_file="f"
        )
        for i in range(60)
    ]
    doc = MarkdownDocument(
        book="BigBook",
        chapter_number=5,
        chapter_title="Huge",
        section_id="",
        author="",
        series="",
        license="Public Domain",
        paragraphs=paras,
    )
    book_output = tmp_path / "BigBook"
    book_output.mkdir()
    with caplog.at_level(logging.INFO, logger=P1.__name__):
        split = P1._save_split_chapter(doc, book_output, logging.getLogger(P1.__name__))
    assert split is True
    assert "OBSERV phase1.split book=BigBook chapter=5" in caplog.text
    assert "chunks_written=" in caplog.text
    assert "chunks_dropped=" in caplog.text
    # At least one chunk actually written to disk.
    assert list(book_output.glob("*-parsed.json"))


# --- "did we get the legacy code?" — Lambda serializer convergence + roman reuse ---


def test_lambda_doc_to_output_matches_cli_shape():
    """Lambda _doc_to_output must delegate to the canonical phase1_parse._doc_to_dict so the
    Lambda path emits is_quote/quote_attribution/table_hints (was a divergent copy that dropped
    them — Phase-1 review P2-A drift)."""
    import lambda_handlers.phase1_handler as H
    import phase1_parse as P

    class Para:
        absolute_number = 3
        text = "He said."
        page_number = 12
        section_id = ""
        source_file = "c.md"
        is_quote = True
        quote_attribution = "Bradley"

    class Doc:
        book = "B"
        chapter_number = 1
        chapter_title = "t"
        section_id = ""
        author = ""
        series = ""
        license = ""
        file_path = "c.md"
        source_url = "https://ex.com/src"
        paragraphs = [Para()]
        images = []
        maps = []
        footnotes = []
        table_hints = ["roster"]

    doc = Doc()
    out = H._doc_to_output(doc)
    # delegation path (not the minimal fallback) -> carries quote provenance + table_hints
    assert out == P._doc_to_dict(doc)
    assert out["paragraphs"][0].get("is_quote") is True
    assert out["paragraphs"][0].get("quote_attribution") == "Bradley"
    assert out.get("table_hints") == ["roster"]


def test_roman_reuses_canonical_parser():
    """discovery._roman_to_int delegates to the single canonical _parse_roman (no duplicate)."""
    from src.discovery import _roman_to_int
    from src.dedup.unit_key import _parse_roman

    for r in ("ii", "iv", "ix", "xiv", "lxvi"):
        assert _roman_to_int(r) == _parse_roman(r)
