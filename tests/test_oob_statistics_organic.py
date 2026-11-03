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


ORGANIC_TEXT_MD = """
29th Infantry Division

ORGANIC UNITS

115th Infantry ..... 29th Reconnaissance Troop (Mecz)
 116th Infantry ..... 121st Engineer Combat Battalion

29th Division Artillery

110th Field Artillery Battalion (105 Howitzer)
 227th Field Artillery Battalion (155 Howitzer)

Special Troops

29th Signal Company
"""


def test_organic_units_text_form_two_column() -> None:
    result = parse_organic_units(ORGANIC_TEXT_MD, "t.md")
    names = {r.unit_name for r in result.rows}
    # Both columns of a dot-leader line are captured.
    assert "115th Infantry" in names
    assert "29th Reconnaissance Troop (Mecz)" in names
    assert "121st Engineer Combat Battalion" in names


def test_organic_units_text_form_single_column_lists() -> None:
    result = parse_organic_units(ORGANIC_TEXT_MD, "t.md")
    names = {r.unit_name for r in result.rows}
    # Single-column artillery / special-troops lists are captured...
    assert "110th Field Artillery Battalion (105 Howitzer)" in names
    assert "29th Signal Company" in names
    # ...but the sub-headers themselves are not emitted as units.
    assert "29th Division Artillery" not in names
    assert "Special Troops" not in names


def test_organic_units_text_form_division_tracked() -> None:
    result = parse_organic_units(ORGANIC_TEXT_MD, "t.md")
    assert result.rows
    assert all(r.division == "29th Infantry Division" for r in result.rows)


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


# --- M3 provenance: raw_text captured -----------------------------------


def test_statistics_rows_carry_raw_text_provenance() -> None:
    """M3: every parsed statistic row must carry the verbatim source line."""
    result = parse_statistics(STATS_MD, "t.md")
    assert result.rows, "expected some rows"
    for r in result.rows:
        assert r.raw_text.strip(), f"row {r.metric!r} has no raw_text provenance"
    # The verbatim line is preserved (metric + value as read).
    activated = [r for r in result.rows if r.metric == "Activated"][0]
    assert "25 Mar 42" in activated.raw_text
    assert "Activated" in activated.raw_text


# --- V1 anti-swallow: in-region unparsed lines are surfaced -------------

_STATS_WITH_GARBLE = """
90th Infantry Division

STATISTICS

Casualties

Killed..... 1,342
@#%^ garbled ocr line with no dot leader structure here
 Wounded..... 5,673
"""


def test_statistics_in_region_drop_emits_observ(caplog) -> None:
    """V1: a non-blank line inside a recognized STATISTICS category that does not
    parse must emit a loud OBSERV oob.rows_dropped (not silently vanish)."""
    import logging

    with caplog.at_level(logging.WARNING):
        result = parse_statistics(_STATS_WITH_GARBLE, "garble.md")
    # The real stat lines still parsed.
    assert any(r.metric == "Killed" for r in result.rows)
    assert any(r.metric == "Wounded" for r in result.rows)
    # The garbled in-region line was surfaced, not swallowed.
    assert any(
        "OBSERV oob.rows_dropped" in r.message
        and "section=statistics" in r.message
        and "file=garble.md" in r.message
        and "status=warn" in r.message
        for r in caplog.records
    ), "in-region unparsed line was silently dropped (no OBSERV)"


def test_statistics_clean_emits_no_rows_dropped(caplog) -> None:
    """V1: no false alarm — a clean parse (every in-region line parses) emits nothing."""
    import logging

    with caplog.at_level(logging.WARNING):
        parse_statistics(STATS_MD, "clean.md")
    assert not any("OBSERV oob.rows_dropped" in r.message for r in caplog.records)


_STATS_TABLE_MD = """
77th Infantry Division

STATISTICS

Casualties

<table><tbody>
<tr><td>Killed</td><td>1,342</td></tr>
<tr><td>Wounded</td><td>5,673</td></tr>
<tr><td>Congressional Medal of Honor</td><td>2</td></tr>
</tbody></table>
"""


def test_statistics_table_encoded_no_false_rows_dropped(caplog) -> None:
    """V1 regression: a TABLE-encoded STATISTICS file must NOT emit oob.rows_dropped —
    the table pass recovers the cell content, so text-pass 'unparsed' lines are not drops
    (this was a systematic false alarm on the real corpus)."""
    import logging

    with caplog.at_level(logging.WARNING):
        result = parse_statistics(_STATS_TABLE_MD, "table.md")
    assert any(r.metric == "Killed" for r in result.rows)
    assert not any(
        "OBSERV oob.rows_dropped" in r.message for r in caplog.records
    ), "table-encoded file emitted a false rows_dropped (recovered content reported as lost)"
