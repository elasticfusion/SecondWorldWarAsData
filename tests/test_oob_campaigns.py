"""Tests for the OOB CAMPAIGNS markdown parser (Piece 2, increment 2)."""

from pathlib import Path

import pytest

from src.ingestion.oob_markdown.campaigns import parse_campaigns, parse_campaigns_file

OOB_DIR = Path(
    "contentrepository/European Thater of Operations - Order of Battle/ocr_output"
)

PLAIN_MD = """
90th Infantry Division

STATISTICS

Campaigns

Normandy
 Northern France
 Ardennes
 Rhineland
 Central Europe

Casualties (Tentative)
Killed..... 533
"""

TABLE_MD = """
4th Infantry Division

STATISTICS

<table border="0">
<thead>
<tr><th colspan="2"><u>Chronology</u></th><th><u>Campaigns</u></th></tr>
</thead>
<tbody>
<tr><td>Activated .....</td><td>25 Mar 42</td><td>Normandy</td></tr>
<tr><td>Arrived ETO .....</td><td>5 Apr 44</td><td>Northern France</td></tr>
<tr><td>Days in Combat .....</td><td>199</td><td>Rhineland</td></tr>
</tbody>
</table>
"""


def test_plain_text_campaigns() -> None:
    result = parse_campaigns(PLAIN_MD, "t.md")
    names = [r.campaign for r in result.rows]
    assert names == [
        "Normandy",
        "Northern France",
        "Ardennes",
        "Rhineland",
        "Central Europe",
    ]
    assert all(r.division == "90th Infantry Division" for r in result.rows)
    # Casualty stat lines after the campaigns list are not captured.
    assert "533" not in " ".join(names)


def test_plain_text_stops_at_statistics() -> None:
    result = parse_campaigns(PLAIN_MD, "t.md")
    assert not any("Killed" in r.campaign for r in result.rows)


def test_table_form_campaigns() -> None:
    result = parse_campaigns(TABLE_MD, "t.md")
    names = [r.campaign for r in result.rows]
    # Only the campaigns column values (known campaigns) are captured, not the
    # chronology columns.
    assert "Normandy" in names
    assert "Northern France" in names
    assert "Rhineland" in names
    assert "25 Mar 42" not in names
    assert "Activated" not in " ".join(names)


def test_known_campaigns_not_flagged() -> None:
    result = parse_campaigns(PLAIN_MD, "t.md")
    assert all(not r.needs_review for r in result.rows)


def test_unusual_value_captured_but_flagged() -> None:
    md = "5th Infantry Division\nSTATISTICS\nCampaigns\nNo Combat\n\nCasualties\n"
    result = parse_campaigns(md, "t.md")
    values = {r.campaign: r for r in result.rows}
    assert "No Combat" in values
    assert values["No Combat"].needs_review is True  # not in known list -> verify


@pytest.mark.skipif(
    not (OOB_DIR / "4th_infantry.md").exists(),
    reason="OOB markdown corpus not present (gitignored content)",
)
def test_real_file_campaigns() -> None:
    result = parse_campaigns_file(OOB_DIR / "4th_infantry.md")
    names = {r.campaign for r in result.rows}
    # 4th Infantry fought these ETO campaigns.
    assert {"Normandy", "Rhineland", "Central Europe"} <= names
