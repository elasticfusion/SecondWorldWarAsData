"""Tests for reviewed-snippet substitution in submit_ocr_job.merge_outputs.

Covers _merge_chunk_with_reviews (page-level substitution) without AWS.
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


def _sep(i: int) -> str:
    return "\n\n" + str(i) + "-" * 48 + "\n\n"


def test_substitutes_reviewed_page():
    # chunk starts at physical page 155; two pages (155, 156).
    chunk = "RAW PAGE 155" + _sep(1) + "RAW PAGE 156 flattened"
    reviewed = {156: ("| CC-A | CC-B |\n|--|--|", "ocr-output/b/reviewed/p156.md")}
    out = soj._merge_chunk_with_reviews(chunk, "chunk-p0155-0156", reviewed)
    assert "RAW PAGE 155" in out
    assert "RAW PAGE 156 flattened" not in out
    assert "| CC-A | CC-B |" in out


def test_no_reviewed_returns_unchanged():
    chunk = "RAW PAGE 155" + _sep(1) + "RAW PAGE 156"
    assert soj._merge_chunk_with_reviews(chunk, "chunk-p0155-0156", {}) == chunk


def test_reviewed_page_outside_chunk_no_change():
    chunk = "RAW PAGE 155" + _sep(1) + "RAW PAGE 156"
    reviewed = {999: ("x", "k")}
    assert soj._merge_chunk_with_reviews(chunk, "chunk-p0155-0156", reviewed) == chunk


def test_legacy_chunk_dir_no_substitution():
    # Legacy index dir has no page range → cannot map → unchanged.
    chunk = "RAW"
    reviewed = {1: ("x", "k")}
    assert soj._merge_chunk_with_reviews(chunk, "chunk-000", reviewed) == chunk
