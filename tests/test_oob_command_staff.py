"""Tests for the OOB COMMAND AND STAFF markdown parser (Piece 2, increment 1)."""

from pathlib import Path

import pytest

from src.ingestion.oob_markdown.command_staff import (
    UNKNOWN_DIVISION,
    parse_command_staff,
    parse_command_staff_file,
)

OOB_DIR = Path(
    "contentrepository/European Thater of Operations - Order of Battle/ocr_output"
)

ROWSPAN_MD = """
## 8 2 d A I R B O R N E D I V I S I O N

COMMAND AND STAFF

<table>
<tbody>
<tr><td rowspan="3">Comdg Gen</td><td>15 Sep 1943</td><td>Maj Gen William C Lee</td></tr>
<tr><td>14 Mar 1944</td><td>Brig Gen Maxwell D Taylor</td></tr>
<tr><td>5 Dec 1944</td><td>Brig Gen Anthony C McAuliffe (actg)</td></tr>
</tbody>
</table>
"""

EMPTY_TD_MD = """
101st Airborne Division

COMMAND AND STAFF

<table><tbody>
<tr><td>CO 502d Prcht<br/>Inf</td><td>15 Sep 1943</td><td>Col George V H Moseley Jr</td></tr>
<tr><td></td><td>9 Jun 1944</td><td>Col J H Michaelis</td></tr>
<tr><td></td><td>23 Sep 1944</td><td>Col Steve A Chappuis</td></tr>
</tbody></table>
"""

GARBLE_MD = """
1st Infantry Division

COMMAND AND STAFF

<table><tbody>
<tr><td>Comdg Gen</td><td>1 Nov 1944</td><td>Br1g G3n @#$ Kohls</td></tr>
<tr><td>CofS</td><td>1 Nov 1944</td><td></td></tr>
</tbody></table>
"""


# --- rowspan grouping ----------------------------------------------------


def test_rowspan_groups_position_across_rows() -> None:
    result = parse_command_staff(ROWSPAN_MD, "test.md")
    assert len(result.rows) == 3
    assert all(r.position == "Comdg Gen" for r in result.rows)
    assert all(r.division == "82d Airborne Division" for r in result.rows)
    assert result.rows[0].rank == "Maj Gen"
    assert result.rows[0].name == "William C Lee"
    assert result.rows[0].effective_date == "15 Sep 1943"


def test_acting_flag_detected_and_stripped() -> None:
    result = parse_command_staff(ROWSPAN_MD, "test.md")
    acting_row = result.rows[2]
    assert acting_row.acting is True
    assert "actg" not in acting_row.name.lower()
    assert acting_row.name == "Anthony C McAuliffe"


# --- empty-td continuation ----------------------------------------------


def test_empty_td_continues_previous_position() -> None:
    result = parse_command_staff(EMPTY_TD_MD, "test.md")
    assert len(result.rows) == 3
    # All three inherit the CO 502d Prcht Inf position.
    assert all("502d Prcht" in r.position for r in result.rows)
    # <br/> collapsed to a space.
    assert "\n" not in result.rows[0].position


def test_br_and_entities_cleaned() -> None:
    md = (
        "1st Infantry Division\nCOMMAND AND STAFF\n"
        "<table><tbody><tr><td>CofS</td><td>1 Nov 1944</td>"
        "<td>Col A B &amp; C Smith</td></tr></tbody></table>"
    )
    result = parse_command_staff(md, "t.md")
    assert "&amp;" not in result.rows[0].name
    assert "&" in result.rows[0].name  # entity decoded


# --- verification / flagging (not correction) ---------------------------


def test_garbled_and_empty_cells_flagged_not_corrected() -> None:
    result = parse_command_staff(GARBLE_MD, "t.md")
    assert len(result.rows) == 2
    garbled = result.rows[0]
    empty = result.rows[1]
    # Garbled rank+name flagged; raw preserved, not "fixed".
    assert garbled.needs_review is True
    assert garbled.raw_cell == "Br1g G3n @#$ Kohls"
    # Empty name flagged.
    assert empty.needs_review is True
    assert "empty name" in empty.notes
    assert empty.name == ""


def test_division_normalization() -> None:
    result = parse_command_staff(ROWSPAN_MD, "t.md")
    # Letter-spaced title collapses to a clean, correctly-cased division.
    assert result.rows[0].division == "82d Airborne Division"


# --- leading title-less table (unknown division, captured not dropped) ---


def test_leading_titleless_table_captured_as_unknown_and_flagged() -> None:
    # A COMMAND AND STAFF table with no preceding division title must be
    # captured (not dropped) and flagged with an unknown division.
    md = (
        "COMMAND AND STAFF\n"
        "<table><tbody>"
        "<tr><td>Comdg Gen</td><td>1 Nov 1944</td>"
        "<td>Brig Gen Albert C Smith</td></tr>"
        "</tbody></table>"
    )
    result = parse_command_staff(md, "1st_infantry.md")
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.division == UNKNOWN_DIVISION
    assert row.needs_review is True
    assert "division unknown" in row.notes
    # Content still parsed correctly despite the unknown division.
    assert row.rank == "Brig Gen"
    assert row.name == "Albert C Smith"


# --- real-file smoke (division-bleed + known-good rows) ------------------


@pytest.mark.skipif(
    not (OOB_DIR / "101st_airborne.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_101st_airborne() -> None:
    result = parse_command_staff_file(OOB_DIR / "101st_airborne.md")
    assert result.rows, "expected command-staff rows"
    # Despite the file opening with 100th Infantry content (bleed), the
    # command-staff table falls under the 101st Airborne title.
    divisions = {r.division for r in result.rows}
    assert "101st Airborne Division" in divisions
    # Known-good first row parses correctly.
    first = result.rows[0]
    assert first.position == "Comdg Gen"
    assert first.rank == "Maj Gen"
    assert first.name == "William C Lee"
    assert first.effective_date == "15 Sep 1943"
