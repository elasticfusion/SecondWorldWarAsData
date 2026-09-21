"""Tests for the OOB ATTACHMENTS / DETACHMENTS markdown parser."""

from pathlib import Path

import pytest

from src.ingestion.oob_markdown.attachments import (
    KIND_ATTACHED,
    KIND_DETACHED,
    parse_attachments,
    parse_attachments_file,
)

OOB_DIR = Path(
    "contentrepository/European Thater of Operations - Order of Battle/ocr_output"
)

ATTACH_MD = """
90th Infantry Division

ATTACHMENTS

Antiaircraft Artillery

898th AAA AW Bn (Mbl)..... 7 Nov 44 - 11 May 45

Armored

781st Tk Bn..... 7 Dec 44 - 21 Dec 44
Co A 47th Tk Bn (14th Armd Div)..... 3 Dec 44 - 6 Dec 44

DETACHMENTS

Infantry

357th Inf..... 1 Jan 45 - 5 Jan 45
"""


def test_attachments_grouped_by_arm() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    by_unit = {r.unit: r for r in rows}
    assert by_unit["898th AAA AW Bn (Mbl)"].arm == "Antiaircraft Artillery"
    assert by_unit["781st Tk Bn"].arm == "Armored"


def test_attachments_date_range_split() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    row = next(r for r in rows if r.unit == "898th AAA AW Bn (Mbl)")
    assert row.start_date == "7 Nov 44"
    assert row.end_date == "11 May 45"


def test_attachments_parenthetical_parent_kept_in_unit() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    assert any(r.unit == "Co A 47th Tk Bn (14th Armd Div)" for r in rows)


def test_attachments_kind_distinguishes_sections() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    attached = {r.unit for r in rows if r.kind == KIND_ATTACHED}
    detached = {r.unit for r in rows if r.kind == KIND_DETACHED}
    assert "898th AAA AW Bn (Mbl)" in attached
    assert "357th Inf" in detached


def test_attachments_division_tracked_from_title() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    assert rows
    assert all(r.division == "90th Infantry Division" for r in rows)
    assert all(r.needs_review is False for r in rows)  # title -> unflagged


def test_attachments_clean_rows_not_flagged() -> None:
    result = parse_attachments(ATTACH_MD, "t.md")
    assert result.review_count == 0


@pytest.mark.skipif(
    not (OOB_DIR / "100th_infantry.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_attachments() -> None:
    result = parse_attachments_file(OOB_DIR / "100th_infantry.md")
    assert result.rows
    # 100th Infantry has both attached and detached units across several arms.
    kinds = {r.kind for r in result.rows}
    assert KIND_ATTACHED in kinds
    arms = {r.arm for r in result.rows}
    assert "Armored" in arms and "Field Artillery" in arms
