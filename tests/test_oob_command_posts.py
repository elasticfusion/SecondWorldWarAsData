"""Tests for the OOB COMMAND POSTS markdown parser (Piece 2, increment 2)."""

from pathlib import Path

import pytest

from src.ingestion.oob_markdown.command_posts import (
    parse_command_posts,
    parse_command_posts_file,
)

OOB_DIR = Path(
    "contentrepository/European Thater of Operations - Order of Battle/ocr_output"
)

CP_MD = """
90th Infantry Division

COMMAND POSTS

<table border="1">
<thead><tr><th>DATE</th><th>TOWN</th><th>REGION</th><th>COUNTRY</th></tr></thead>
<tbody>
<tr><td><u>1944</u><br/>5 Apr</td><td>Liverpool</td><td>Lancashire</td><td>England</td></tr>
<tr><td>30 Apr</td><td>King Edward's School</td><td></td><td>England</td></tr>
<tr><td>8 Jun</td><td>Loutres</td><td>Manche</td><td>France</td></tr>
<tr><td><u>1945</u><br/>1 Feb</td><td>Cologne</td><td>Rhineland</td><td>Germany</td></tr>
</tbody>
</table>
"""


def test_year_inherited_across_rows() -> None:
    result = parse_command_posts(CP_MD, "t.md")
    assert len(result.rows) == 4
    # First three inherit 1944; the fourth switches to 1945.
    assert result.rows[0].year == "1944"
    assert result.rows[0].date == "5 Apr"
    assert result.rows[1].year == "1944"  # inherited (no year in the cell)
    assert result.rows[1].date == "30 Apr"
    assert result.rows[2].year == "1944"
    assert result.rows[3].year == "1945"  # new context year
    assert result.rows[3].date == "1 Feb"


def test_town_region_country_captured() -> None:
    result = parse_command_posts(CP_MD, "t.md")
    first = result.rows[0]
    assert first.town == "Liverpool"
    assert first.region == "Lancashire"
    assert first.country == "England"
    assert first.division == "90th Infantry Division"


def test_empty_region_preserved_not_flagged_alone() -> None:
    result = parse_command_posts(CP_MD, "t.md")
    # Row 2 has an empty region but a valid town/date/year -> not flagged.
    row = result.rows[1]
    assert row.region == ""
    assert row.needs_review is False


def test_header_row_skipped() -> None:
    result = parse_command_posts(CP_MD, "t.md")
    # No row should be the DATE/TOWN/REGION/COUNTRY header.
    assert all(r.town != "TOWN" for r in result.rows)


@pytest.mark.skipif(
    not (OOB_DIR / "90th_infantry.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_command_posts() -> None:
    result = parse_command_posts_file(OOB_DIR / "90th_infantry.md")
    assert result.rows
    # Every row should have a year established via inheritance.
    assert all(r.year for r in result.rows if not r.needs_review)
    # Known first CP.
    assert result.rows[0].town == "Liverpool"
    assert result.rows[0].year == "1944"
