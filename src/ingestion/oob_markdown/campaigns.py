"""Parse CAMPAIGNS content from Chandra OCR+AI markdown into rows.

Campaigns appear in two forms across the corpus (validated: ~39 files plain
text, ~17 files table):

* **Plain-text list** under a ``Campaigns`` header, one campaign per line.
* **Table column** — the last column of the STATISTICS chronology table, under a
  ``Campaigns`` header cell (chronology and campaigns side by side).

Verification, not correction: suspect values are flagged, never rewritten.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup, Tag

from src.ingestion.oob_markdown._common import (
    DIVISION_SOURCE_TITLE,
    SECTION_CAMPAIGNS,
    apply_division_flag,
    attribute_division,
    clean_cell,
    division_from_line,
    division_from_recon_troop,
    first_division_in,
    iter_section_text_blocks,
)
from src.ingestion.oob_markdown.models import CampaignParseResult, CampaignRow

logger = logging.getLogger(__name__)

# Known ETO campaign names (used to recognize campaign cells in the chronology
# table form, where campaigns share a table with unrelated chronology values).
KNOWN_CAMPAIGNS = (
    "Normandy",
    "Northern France",
    "Rhineland",
    "Ardennes",
    "Ardennes-Alsace",
    "Central Europe",
    "Southern France",
    "Sicily",
    "Naples-Foggia",
    "Rome-Arno",
    "Po Valley",
    "North Apennines",
)

# Lines in the plain-text campaigns block that are actually sub-headers of the
# next STATISTICS sub-block, not campaigns.
_NOT_A_CAMPAIGN = re.compile(
    r"^(Casualties|Individual Awards|Chronology|Statistics|Composition|"
    r"Killed|Wounded|Missing|Captured|Total|Non-Battle|Battle|DSC|"
    r"Silver Star|Legion|Bronze|Air Medal|Soldier|Distinguished)\b",
    re.IGNORECASE,
)

# A campaign name is short and word-like. Dot-leaders (".....") signal a
# statistic (key..... value), not a campaign, so such lines terminate the list.
_STAT_LINE_RE = re.compile(r"\.{2,}")


def _assess_campaign(name: str, from_table: bool) -> tuple[float, bool, str]:
    """Verification only: flag campaigns that need a human check.

    Known campaign names are trusted. Plain-text values not in the known list
    (unusual-but-real like "No Combat", or OCR hyphenation fragments) are
    captured but flagged for review rather than silently trusted or dropped.
    """
    if not name:
        return 0.5, True, "empty campaign"
    if name in KNOWN_CAMPAIGNS:
        return 0.95, False, ""
    if from_table:
        return 0.9, False, ""
    return 0.5, True, "campaign not in known list; verify (may be OCR fragment)"


def _row(
    division: str,
    name: str,
    source_file: str,
    from_table: bool,
    division_source: str = DIVISION_SOURCE_TITLE,
) -> CampaignRow:
    confidence, needs_review, notes = _assess_campaign(name, from_table)
    confidence, needs_review, notes = apply_division_flag(
        division_source, confidence, needs_review, notes
    )
    return CampaignRow(
        division=division,
        campaign=name,
        confidence=confidence,
        needs_review=needs_review,
        notes=notes,
        source_file=source_file,
        division_source=division_source,
    )


def _looks_like_campaign(name: str) -> bool:
    """True if a plain-text line is plausibly a campaign name.

    Known campaigns always pass. Otherwise require a short, clean, digit-free
    letters-only phrase (>= 4 chars) without dot-leaders, parentheticals,
    hyphenation tails, all-caps OCR noise, or stat sub-headers — enough to
    exclude interleaved chronology/statistics text while still admitting
    campaign names not in the known list.
    """
    if name in KNOWN_CAMPAIGNS:
        return True
    rejects = (
        len(name) < 4,
        any(ch.isdigit() for ch in name),
        any(ch in name for ch in "()."),
        name.endswith("-"),
        name.isupper(),
        bool(_NOT_A_CAMPAIGN.match(name)),
    )
    if any(rejects):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z \-]+", name))


def _parse_plain_text(markdown: str, source_file: str) -> List[CampaignRow]:
    """Parse the plain-text 'Campaigns' list form.

    Within a campaigns text block, campaign names are a short contiguous list.
    A dot-leader line ("key..... value") or a stat sub-header terminates the
    list; interleaved chronology/statistics lines are skipped via a
    campaign-shape check.
    """
    rows: List[CampaignRow] = []
    for division, source, lines in iter_section_text_blocks(
        markdown, SECTION_CAMPAIGNS
    ):
        for line in lines:
            name = line.strip()
            if not name:
                continue
            if _STAT_LINE_RE.search(name) or _NOT_A_CAMPAIGN.match(name):
                break
            if not _looks_like_campaign(name):
                continue
            rows.append(
                _row(
                    division,
                    name,
                    source_file,
                    from_table=False,
                    division_source=source,
                )
            )
    return rows


def _track_division(text: str, division: str) -> str:
    """Update the current division from a text node's lines."""
    for line in text.splitlines():
        div = division_from_line(line)
        if div:
            division = div
    return division


def _campaign_cells(table: Tag) -> List[str]:
    """Return the campaigns-column cell texts of a table (empty if no column)."""
    col = _campaigns_column_index(table)
    if col is None:
        return []
    values: List[str] = []
    for tr in table.find_all("tr"):
        cells = list(tr.find_all(["td", "th"]))
        if col < len(cells):
            values.append(clean_cell(cells[col]))
    return values


def _parse_table_form(markdown: str, source_file: str) -> List[CampaignRow]:
    """Parse campaigns from the chronology+campaigns table form.

    Collects cells in a table's 'Campaigns' column that match a known campaign
    name, tracking the current division across the document. A campaigns table
    that precedes any division title is attributed by the same Signal 1
    (next-title) / Signal 2 (recon-troop) inference the other section parsers
    use via :func:`attribute_division`, so a leading STATISTICS/Campaigns table
    is no longer left ``(unknown)`` when the file's title appears later.
    """
    rows: List[CampaignRow] = []
    soup = BeautifulSoup(markdown, "html.parser")
    inferred = first_division_in(markdown)
    recon = division_from_recon_troop(markdown)
    division = ""
    for element in soup.descendants:
        if not isinstance(element, Tag):
            division = _track_division(str(element), division)
            continue
        if element.name != "table":
            continue
        div, source = attribute_division(division, inferred, recon)
        for name in _campaign_cells(element):
            if name in KNOWN_CAMPAIGNS:
                rows.append(
                    _row(
                        div, name, source_file, from_table=True, division_source=source
                    )
                )
    return rows


def _campaigns_column_index(table: Tag) -> Optional[int]:
    """Return the BODY column index of the 'Campaigns' header, if present.

    Accounts for ``colspan`` on preceding header cells: a ``<th colspan="2">``
    occupies two body columns, so header-cell position is not the body-column
    index.
    """
    for tr in table.find_all("tr"):
        headers = tr.find_all("th")
        if not headers:
            continue
        body_col = 0
        for th in headers:
            span_attr = th.get("colspan", "1")
            span = (
                int(span_attr)
                if isinstance(span_attr, str) and span_attr.isdigit()
                else 1
            )
            if re.search(r"Campaigns", clean_cell(th), re.IGNORECASE):
                return body_col
            body_col += span
    return None


def parse_campaigns(markdown: str, source_file: str = "") -> CampaignParseResult:
    """Parse CAMPAIGNS rows from markdown.

    Prefers the table form when a 'Campaigns' table column exists (campaigns are
    then cleanly delimited by the column, avoiding the interleaved-statistics
    noise of the plain-text form). Falls back to the plain-text list form only
    when no campaigns table column is present.
    """
    result = CampaignParseResult(source_file=source_file)
    table_rows = _parse_table_form(markdown, source_file)
    rows = table_rows if table_rows else _parse_plain_text(markdown, source_file)

    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.division, row.campaign)
        if key in seen:
            continue
        seen.add(key)
        result.rows.append(row)
    return result


def parse_campaigns_file(path: Path) -> CampaignParseResult:
    """Parse CAMPAIGNS rows from a markdown file."""
    result = parse_campaigns(path.read_text(encoding="utf-8"), source_file=path.name)
    logger.info(
        "Parsed %s: %d campaign row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result
