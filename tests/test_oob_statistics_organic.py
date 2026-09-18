"""Tests for OOB STATISTICS + ORGANIC UNITS markdown parsers (Piece 2, inc 3)."""

from pathlib import Path

import pytest

from src.ingestion.oob_markdown.organic_units import (
    parse_organic_units,
    parse_organic_units_file,
)
from src.ingestion.oob_markdown.statistics import (
    parse_statistics,
    parse_statistics_file,
)

OOB_DIR = Path(
    "contentrepository/European Thater of Operations - Order of Battle/ocr_output"
)

STATS_MD = """
90th Infantry Division

STATISTICS

Chronology

Activated..... 25 Mar 42
 Days in Combat..... 199

Campaigns

Normandy

Casualties (Tentative)

Killed..... 1,342
 Wounded..... 5,673
 Total Casualties.....

Individual Awards

DSC..... 12
"""

ORGANIC_MD = """
101st Airborne Division

ORGANIC UNITS

<table border="0">
<tr><td>502d Prcht Inf Regt</td><td>101st Prcht Maint Bn</td></tr>
<tr><td>*506th Prcht Inf Regt</td><td>326th Abn Engr Bn</td></tr>
<tr><td># Band</td><td></td></tr>
</table>
"""


# --- statistics ----------------------------------------------------------


def test_statistics_category_tracking() -> None:
    result = parse_statistics(STATS_MD, "t.md")
    by_cat = {(r.category, r.metric): r.value for r in result.rows}
    assert by_cat[("Chronology", "Activated")] == "25 Mar 42"
    assert by_cat[("Chronology", "Days in Combat")] == "199"
    assert by_cat[("Casualties", "Killed")] == "1,342"
    assert by_cat[("Individual Awards", "DSC")] == "12"


def test_statistics_campaigns_not_captured_as_stat() -> None:
    result = parse_statistics(STATS_MD, "t.md")
    assert not any(r.metric == "Normandy" for r in result.rows)
    assert not any(r.category == "" for r in result.rows)


def test_statistics_empty_value_flagged() -> None:
    result = parse_statistics(STATS_MD, "t.md")
    total = [r for r in result.rows if r.metric == "Total Casualties"]
    assert total and total[0].needs_review is True
    assert "empty value" in total[0].notes


def test_statistics_comma_value_preserved() -> None:
    result = parse_statistics(STATS_MD, "t.md")
    wounded = [r for r in result.rows if r.metric == "Wounded"][0]
    assert wounded.value == "5,673"
    assert wounded.needs_review is False


# --- organic units -------------------------------------------------------


def test_organic_units_two_column_expansion() -> None:
    result = parse_organic_units(ORGANIC_MD, "t.md")
    names = {r.unit_name for r in result.rows}
    assert "502d Prcht Inf Regt" in names
    assert "101st Prcht Maint Bn" in names
    assert "326th Abn Engr Bn" in names


def test_organic_units_glyph_captured_to_notes() -> None:
    result = parse_organic_units(ORGANIC_MD, "t.md")
    starred = [r for r in result.rows if r.unit_name == "506th Prcht Inf Regt"]
    assert starred and starred[0].notes == "*"
    banded = [r for r in result.rows if r.unit_name == "Band"]
    assert banded and banded[0].notes == "#"


def test_organic_units_empty_cell_skipped() -> None:
    result = parse_organic_units(ORGANIC_MD, "t.md")
    assert all(r.unit_name for r in result.rows)


# --- real-file smoke + CSV validation reference --------------------------


@pytest.mark.skipif(
    not (OOB_DIR / "90th_infantry.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_statistics() -> None:
    result = parse_statistics_file(OOB_DIR / "101st_airborne.md")
    assert result.rows
    # A known chronology stat.
    activated = [r for r in result.rows if r.metric == "Activated"]
    assert activated and activated[0].category == "Chronology"


@pytest.mark.skipif(
    not (OOB_DIR / "90th_infantry.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_organic_units() -> None:
    result = parse_organic_units_file(OOB_DIR / "90th_infantry.md")
    names = {r.unit_name for r in result.rows}
    # 90th Infantry organic regiments.
    assert "357th Infantry" in names
    assert "358th Infantry" in names
    assert "359th Infantry" in names
