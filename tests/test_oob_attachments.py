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

# Variant A (e.g. 9th_infantry.md): arm headers are PLAIN TEXT between tables;
# data rows are 4 cells (unit / start / "-" / end). DETACHMENTS rows carry an
# attached-to column.
ATTACH_MD = """
90th Infantry Division

ATTACHMENTS

Antiaircraft Artillery

<table border="0">
<tr><td>898th AAA AW Bn (Mbl).....</td><td>7 Nov 44</td><td>-</td><td>11 May 45</td></tr>
</table>

Armored

<table border="0">
<tr><td>781st Tk Bn.....</td><td>7 Dec 44</td><td>-</td><td>21 Dec 44</td></tr>
<tr><td>Co A 47th Tk Bn (14th Armd Div).....</td><td>3 Dec 44</td><td>-</td><td>6 Dec 44</td></tr>
</table>

DETACHMENTS

(Attached To)

Infantry

<table border="0">
<tr><td>357th Inf.....</td><td>36th Div.....</td><td>1 Jan 45</td><td>-</td><td>5 Jan 45</td></tr>
</table>
"""

# Variant B (e.g. 1st_infantry.md): arm sub-headers are colspan <u> rows INSIDE
# the table; ATTACHMENTS dates are one combined cell ("15 Nov 44 -"); a
# DETACHMENTS range is a single combined cell ("12 Nov 44 - 2 Dec 44").
ATTACH_MD_COLSPAN = """
14th Armored Division

ATTACHMENTS

<table border="0">
<tr><td colspan="3"><u>Antiaircraft Artillery</u></td></tr>
<tr><td>398th AAA Av Bn (SP).....</td><td>15 Nov 44 -</td><td>12 May 45</td></tr>
<tr><td colspan="3"><u>Cavalry</u></td></tr>
<tr><td>117th Cav Rcn Sq.....</td><td>2 Jan 45 -</td><td>10 Jan 45</td></tr>
</table>

DETACHMENTS

(Attached To)

<table border="0">
<tr><td colspan="3"><u>Armored</u></td></tr>
<tr><td>CC R.....</td><td>44th AAA Brig...</td><td>12 Nov 44 - 2 Dec 44</td></tr>
</table>
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


def test_detachment_captures_attached_to() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    det = next(r for r in rows if r.unit == "357th Inf")
    assert det.kind == KIND_DETACHED
    assert det.attached_to == "36th Div"
    assert det.start_date == "1 Jan 45"
    assert det.end_date == "5 Jan 45"


def test_attachment_has_no_attached_to() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    att = next(r for r in rows if r.unit == "898th AAA AW Bn (Mbl)")
    assert att.attached_to == ""


def test_colspan_subheaders_group_arms() -> None:
    rows = parse_attachments(ATTACH_MD_COLSPAN, "t.md").rows
    by_unit = {r.unit: r for r in rows}
    assert by_unit["398th AAA Av Bn (SP)"].arm == "Antiaircraft Artillery"
    assert by_unit["117th Cav Rcn Sq"].arm == "Cavalry"


def test_colspan_open_end_date() -> None:
    rows = parse_attachments(ATTACH_MD_COLSPAN, "t.md").rows
    row = next(r for r in rows if r.unit == "398th AAA Av Bn (SP)")
    # "15 Nov 44 -" combined cell + "12 May 45" second cell.
    assert row.start_date == "15 Nov 44"
    assert row.end_date == "12 May 45"


def test_colspan_detachment_combined_range_split() -> None:
    rows = parse_attachments(ATTACH_MD_COLSPAN, "t.md").rows
    det = next(r for r in rows if r.unit == "CC R")
    assert det.kind == KIND_DETACHED
    assert det.arm == "Armored"
    assert det.attached_to == "44th AAA Brig"
    assert det.start_date == "12 Nov 44"
    assert det.end_date == "2 Dec 44"


def test_attachments_division_tracked_from_title() -> None:
    rows = parse_attachments(ATTACH_MD, "t.md").rows
    assert rows
    assert all(r.division == "90th Infantry Division" for r in rows)
    assert all(r.needs_review is False for r in rows)  # title -> unflagged


def test_attachments_clean_rows_not_flagged() -> None:
    result = parse_attachments(ATTACH_MD, "t.md")
    assert result.review_count == 0


@pytest.mark.skipif(
    not (OOB_DIR / "9th_infantry.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_attachments() -> None:
    result = parse_attachments_file(OOB_DIR / "9th_infantry.md")
    # Real HTML-table extraction: the old plain-text parser got ~zero rows here.
    assert len(result.rows) > 50
    kinds = {r.kind for r in result.rows}
    assert KIND_ATTACHED in kinds and KIND_DETACHED in kinds
    arms = {r.arm for r in result.rows}
    assert "Antiaircraft Artillery" in arms
    # Detachments carry an attached-to formation.
    assert any(r.attached_to for r in result.rows if r.kind == KIND_DETACHED)
