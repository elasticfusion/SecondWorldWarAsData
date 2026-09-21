"""Tests for OOB date-plausibility checks (verification, not correction).

The checker recognizes the corpus military-date forms and flags implausible
dates (bad format, out-of-range day/month, or a year outside the ETO window)
without ever rewriting the value.
"""

from src.ingestion.oob_markdown._common import (
    DATE_WINDOW_MAX_YEAR,
    DATE_WINDOW_MIN_YEAR,
    apply_date_flag,
    check_date_plausibility,
)
from src.ingestion.oob_markdown.command_staff import parse_command_staff
from src.ingestion.oob_markdown.higher_units import parse_higher_units

# --- checker unit tests --------------------------------------------------


def test_valid_dates_pass() -> None:
    assert check_date_plausibility("7 Nov 44") is None
    assert check_date_plausibility("15 Sep 1943") is None
    assert check_date_plausibility("3 Sep") is None  # year-less (command posts)
    assert check_date_plausibility("") is None
    assert check_date_plausibility("16 Dec 1945") is None  # in-window edge


def test_year_outside_window_flagged() -> None:
    assert "outside ETO window" in check_date_plausibility("16 Dec 1955")
    assert "outside ETO window" in check_date_plausibility("1 Jan 1939")
    # A mis-split fragment producing a 3-digit year is caught by the window.
    assert "outside ETO window" in check_date_plausibility("26 Mar 439")


def test_window_bounds_are_inclusive() -> None:
    assert check_date_plausibility(f"1 Jan {DATE_WINDOW_MIN_YEAR}") is None
    assert check_date_plausibility(f"31 Dec {DATE_WINDOW_MAX_YEAR}") is None


def test_bad_day_or_month_flagged() -> None:
    assert "day 32 out of range" in check_date_plausibility("32 Nov 44")
    assert "unknown month" in check_date_plausibility("7 Xyz 44")


def test_unparseable_date_flagged() -> None:
    # A dot-leader unit fragment that leaked into a date cell.
    assert "unparseable" in check_date_plausibility("84th Div ..... 8 Feb 45")
    assert "unparseable" in check_date_plausibility("sometime in 1944")


def test_apply_date_flag_caps_and_notes() -> None:
    conf, review, notes = apply_date_flag("16 Dec 1955", 0.95, False, "")
    assert review is True
    assert conf <= 0.5
    assert "outside ETO window" in notes
    # A good date passes through unchanged.
    assert apply_date_flag("7 Nov 44", 0.95, False, "ok") == (0.95, False, "ok")


# --- end-to-end through parsers ------------------------------------------


def test_command_staff_flags_implausible_date() -> None:
    md = (
        "9th Infantry Division\nCOMMAND AND STAFF\n"
        "<table><tbody><tr><td>Comdg Gen</td><td>1 Nov 1955</td>"
        "<td>Maj Gen John A Smith</td></tr></tbody></table>\n"
    )
    row = parse_command_staff(md, "t.md").rows[0]
    assert row.needs_review is True
    assert "outside ETO window" in row.notes


def test_higher_units_flags_implausible_date() -> None:
    md = (
        "9th Infantry Division\nASSIGNMENT AND ATTACHMENT\n"
        "<table><tbody>"
        "<tr><td>1 Jan 1955</td><td>VII</td><td>First</td>"
        "<td></td><td>12th</td><td></td></tr>"
        "</tbody></table>\n"
    )
    row = parse_higher_units(md, "t.md").rows[0]
    assert row.needs_review is True
    assert "outside ETO window" in row.notes


def test_command_staff_valid_date_not_flagged() -> None:
    md = (
        "9th Infantry Division\nCOMMAND AND STAFF\n"
        "<table><tbody><tr><td>Comdg Gen</td><td>1 Nov 1944</td>"
        "<td>Maj Gen John A Smith</td></tr></tbody></table>\n"
    )
    row = parse_command_staff(md, "t.md").rows[0]
    assert row.needs_review is False
