"""Tests for the OOB ASSIGNMENT AND ATTACHMENT ("To Higher Units") parser."""

from pathlib import Path

import pytest

from src.ingestion.oob_markdown.higher_units import (
    parse_higher_units,
    parse_higher_units_file,
)

OOB_DIR = Path(
    "contentrepository/European Thater of Operations - Order of Battle/ocr_output"
)

HIGHER_MD = """
9th Infantry Division

ASSIGNMENT AND ATTACHMENT

<table border="1">
<thead>
<tr><th rowspan="2">DATE</th><th rowspan="2">CORPS</th>
<th colspan="2">ARMY</th><th colspan="2">ARMY GROUP AND OTHER</th></tr>
<tr><th>Asgd</th><th>Atchd</th><th>Asgd</th><th>Atchd</th></tr>
</thead>
<tbody>
<tr><td>20 Nov 43</td><td></td><td>First</td><td></td><td>ETOUSA</td><td></td></tr>
<tr><td>25 Nov 43</td><td>VII</td><td>First</td><td></td><td></td><td></td></tr>
<tr><td>1 Aug 44</td><td>VII</td><td>First</td><td></td><td>12th</td><td>Br 21st</td></tr>
</tbody>
</table>
"""


def test_higher_units_maps_columns() -> None:
    rows = parse_higher_units(HIGHER_MD, "t.md").rows
    by_date = {r.date: r for r in rows}
    assert by_date["25 Nov 43"].corps == "VII"
    assert by_date["25 Nov 43"].army_assigned == "First"
    assert by_date["1 Aug 44"].group_assigned == "12th"
    assert by_date["1 Aug 44"].group_attached == "Br 21st"


def test_higher_units_skips_header_rows() -> None:
    rows = parse_higher_units(HIGHER_MD, "t.md").rows
    # No row should have DATE/CORPS/Asgd as its "date".
    assert all(r.date not in {"DATE", "CORPS", "Asgd", "Atchd"} for r in rows)
    assert len(rows) == 3


def test_higher_units_division_and_flags() -> None:
    rows = parse_higher_units(HIGHER_MD, "t.md").rows
    assert rows
    assert all(r.division == "9th Infantry Division" for r in rows)
    assert all(r.needs_review is False for r in rows)  # title -> unflagged


def test_higher_units_blank_cells_are_empty_strings() -> None:
    rows = parse_higher_units(HIGHER_MD, "t.md").rows
    first = next(r for r in rows if r.date == "20 Nov 43")
    assert first.corps == ""
    assert first.army_attached == ""
    assert first.group_assigned == "ETOUSA"


def test_higher_units_missing_date_flagged() -> None:
    md = (
        "9th Infantry Division\nASSIGNMENT AND ATTACHMENT\n"
        "<table><tbody>"
        "<tr><td></td><td>VII</td><td>First</td><td></td><td>12th</td><td></td></tr>"
        "</tbody></table>\n"
    )
    rows = parse_higher_units(md, "t.md").rows
    assert rows
    assert rows[0].needs_review is True
    assert "date" in rows[0].notes.lower()


@pytest.mark.skipif(
    not (OOB_DIR / "9th_infantry.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_higher_units() -> None:
    result = parse_higher_units_file(OOB_DIR / "9th_infantry.md")
    assert result.rows
    # 9th Infantry served under First Army for most of the ETO.
    assert any(r.army_assigned == "First" for r in result.rows)
    assert all(r.division == "9th Infantry Division" for r in result.rows)
