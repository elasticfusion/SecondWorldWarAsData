"""Tests for the 1-based-physical -> 0-based-Chandra page-range fix.

Chandra's ``--page-range`` is 0-based and matched against 0-based PDF page
indices (``chandra.input.parse_range_str``). The pipeline speaks 1-based
physical pages; ``submit_ocr_job._to_chandra_range`` converts only the value
handed to Chandra. Passing the 1-based spec straight through dropped the first
page of every range — this locks the fix.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "submit_ocr_job",
    str(Path(__file__).resolve().parents[1] / "scripts" / "submit_ocr_job.py"),
)
soj = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(soj)  # type: ignore[union-attr]


def test_single_page_shifts_to_zero_based():
    assert soj._to_chandra_range("7") == "6"
    assert soj._to_chandra_range("1") == "0"


def test_range_shifts_both_bounds():
    assert soj._to_chandra_range("1-2") == "0-1"
    assert soj._to_chandra_range("1-50") == "0-49"
    assert soj._to_chandra_range("21-34") == "20-33"
    assert soj._to_chandra_range("155-156") == "154-155"


def test_comma_list_shifts_each():
    assert soj._to_chandra_range("21-34,40") == "20-33,39"
    assert soj._to_chandra_range("1,5,9") == "0,4,8"


def test_non_numeric_returned_unchanged():
    assert soj._to_chandra_range("intro") == "intro"


def test_roundtrip_against_chandra_parser_covers_all_physical_pages():
    """Our 0-based output, parsed by Chandra, must cover exactly the intended
    physical pages as 0-based indices (physical N -> index N-1)."""
    from chandra.input import parse_range_str  # pylint: disable=import-error

    # Physical pages 1..50 -> indices 0..49, all present, none dropped/overshot.
    indices = parse_range_str(soj._to_chandra_range("1-50"))
    assert indices == list(range(0, 50))

    # The 2-page case that originally dropped page 1.
    assert parse_range_str(soj._to_chandra_range("1-2")) == [0, 1]

    # Single physical page 7 -> index 6.
    assert parse_range_str(soj._to_chandra_range("7")) == [6]


def test_chunk_dir_stays_one_based_physical():
    """The human-facing chunk dir must remain 1-based (only the Chandra arg
    shifts), so output layout / merge mapping are unaffected by the fix."""
    assert soj._chunk_dir("1-50") == "chunk-p0001-0050"
    assert soj._chunk_dir("155-156") == "chunk-p0155-0156"
