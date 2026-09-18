"""Tests for next-title division inference (Signal 1).

Verifies that a section table/block appearing BEFORE any division title is
attributed to the first title in the file (inferred, flagged), a block under a
title keeps that title (read directly, unflagged), and a file with no title
leaves the division (unknown).
"""

from src.ingestion.oob_markdown._common import (
    DIVISION_SOURCE_INFERRED,
    DIVISION_SOURCE_TITLE,
    DIVISION_SOURCE_UNKNOWN,
    UNKNOWN_DIVISION,
    attribute_division,
    first_division_in,
)
from src.ingestion.oob_markdown.command_staff import parse_command_staff
from src.ingestion.oob_markdown.organic_units import parse_organic_units

# Command-staff table BEFORE any division title, with the title appearing after.
LEADING_MD = """
COMMAND AND STAFF

<table><tbody>
<tr><td>Comdg Gen</td><td>1 Nov 1944</td><td>Maj Gen John A Smith</td></tr>
</tbody></table>

## 8 2 d A I R B O R N E D I V I S I O N
"""

# Table UNDER a title (normal case).
UNDER_TITLE_MD = """
82d Airborne Division

COMMAND AND STAFF

<table><tbody>
<tr><td>Comdg Gen</td><td>1 Nov 1944</td><td>Maj Gen John A Smith</td></tr>
</tbody></table>
"""

# No division title anywhere.
NO_TITLE_MD = """
COMMAND AND STAFF

<table><tbody>
<tr><td>Comdg Gen</td><td>1 Nov 1944</td><td>Maj Gen John A Smith</td></tr>
</tbody></table>
"""


# --- helper unit tests ---------------------------------------------------


def test_first_division_in_finds_later_title() -> None:
    assert first_division_in(LEADING_MD) == "82d Airborne Division"
    assert first_division_in(NO_TITLE_MD) is None


def test_attribute_division_precedence() -> None:
    # A seen (title) division always wins.
    assert attribute_division("4th Armored Division", "82d Airborne Division") == (
        "4th Armored Division",
        DIVISION_SOURCE_TITLE,
    )
    # No seen division but an inferred one -> inferred.
    assert attribute_division("", "82d Airborne Division") == (
        "82d Airborne Division",
        DIVISION_SOURCE_INFERRED,
    )
    # Nothing -> unknown.
    assert attribute_division("", None) == (UNKNOWN_DIVISION, DIVISION_SOURCE_UNKNOWN)


# --- end-to-end through a parser -----------------------------------------


def test_leading_table_inferred_from_next_title() -> None:
    rows = parse_command_staff(LEADING_MD, "t.md").rows
    assert rows
    row = rows[0]
    assert row.division == "82d Airborne Division"
    assert row.division_source == DIVISION_SOURCE_INFERRED
    assert row.needs_review is True  # inferred -> flagged for verification
    assert "inferred" in row.notes.lower()


def test_table_under_title_is_read_directly() -> None:
    rows = parse_command_staff(UNDER_TITLE_MD, "t.md").rows
    assert rows
    row = rows[0]
    assert row.division == "82d Airborne Division"
    assert row.division_source == DIVISION_SOURCE_TITLE
    assert row.needs_review is False  # read directly -> not flagged


def test_no_title_stays_unknown() -> None:
    rows = parse_command_staff(NO_TITLE_MD, "t.md").rows
    assert rows
    row = rows[0]
    assert row.division == UNKNOWN_DIVISION
    assert row.division_source == DIVISION_SOURCE_UNKNOWN
    assert row.needs_review is True


def test_inference_never_overwrites_a_known_division() -> None:
    # Two divisions in one file: rows under each title keep their own title,
    # never the "first" title.
    md = (
        "4th Armored Division\nCOMMAND AND STAFF\n"
        "<table><tbody><tr><td>Comdg Gen</td><td>1 Nov 1944</td>"
        "<td>Maj Gen A B First</td></tr></tbody></table>\n"
        "5th Armored Division\nCOMMAND AND STAFF\n"
        "<table><tbody><tr><td>Comdg Gen</td><td>1 Nov 1944</td>"
        "<td>Maj Gen C D Second</td></tr></tbody></table>\n"
    )
    rows = parse_command_staff(md, "t.md").rows
    by_name = {r.name: r for r in rows}
    assert by_name["A B First"].division == "4th Armored Division"
    assert by_name["A B First"].division_source == DIVISION_SOURCE_TITLE
    assert by_name["C D Second"].division == "5th Armored Division"
    assert by_name["C D Second"].division_source == DIVISION_SOURCE_TITLE


def test_organic_units_inference_applies_too() -> None:
    md = (
        "ORGANIC UNITS\n"
        "<table border='0'><tr><td>357th Infantry</td>"
        "<td>90th Reconnaissance Troop</td></tr></table>\n"
        "## 9 0 th I N F A N T R Y D I V I S I O N\n"
    )
    rows = parse_organic_units(md, "t.md").rows
    assert rows
    assert all(r.division == "90th Infantry Division" for r in rows)
    assert all(r.division_source == DIVISION_SOURCE_INFERRED for r in rows)
    assert all(r.needs_review for r in rows)
