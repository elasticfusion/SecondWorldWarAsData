"""Parse ASSIGNMENT AND ATTACHMENT ("To Higher Units") content from markdown.

This section records a division's higher chain of command over time as an HTML
table: per effective date, the corps the division was under and the army /
army-group it was assigned (``Asgd``) or attached (``Atchd``) to. The header is
two rows — ``DATE | CORPS | ARMY | ARMY GROUP AND OTHER`` then ``Asgd | Atchd``
sub-headers under ARMY and ARMY GROUP — over data rows of six cells:
``date, corps, army_asgd, army_atchd, group_asgd, group_atchd``.

Blank cells mean unchanged/none; a literal ``-`` is preserved as read. Verifying,
not correcting: a row whose date is missing/implausible is flagged, never
rewritten — the same posture as the other OOB parsers.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from bs4 import Tag

from src.ingestion.oob_markdown._common import (
    SECTION_HIGHER_UNITS,
    apply_date_flag,
    apply_division_flag,
    clean_cell,
    iter_section_tables,
)
from src.ingestion.oob_markdown.models import (
    HigherUnitParseResult,
    HigherUnitRow,
)

logger = logging.getLogger(__name__)

# Cells that identify a header row (to skip): the column labels of the table.
_HEADER_TOKENS = frozenset(
    {
        "date",
        "corps",
        "army",
        "army group and other",
        "asgd",
        "atchd",
    }
)

# Expected data-row width: date, corps, army_asgd, army_atchd, group_asgd,
# group_atchd.
_EXPECTED_CELLS = 6


def _is_header_row(cells: List[str]) -> bool:
    """True if a row is one of the two header rows (all cells are labels)."""
    non_empty = [c for c in cells if c]
    if not non_empty:
        return True
    return all(c.strip().lower() in _HEADER_TOKENS for c in non_empty)


def _row_from_cells(
    cells: List[str], division: str, division_source: str, source_file: str
) -> Optional[HigherUnitRow]:
    """Build a HigherUnitRow from a data row's cells, or None if unusable.

    Pads/truncates to the expected six columns so a short OCR row still yields
    what it has. A row with no date is flagged (the date is the row's key).
    """
    padded = (cells + [""] * _EXPECTED_CELLS)[:_EXPECTED_CELLS]
    date, corps, army_asgd, army_atchd, group_asgd, group_atchd = padded
    date = date.strip()
    if not any(padded):
        return None

    notes = ""
    needs_review = False
    if not date:
        notes = "missing date"
        needs_review = True
    confidence = 0.95 if not needs_review else 0.5
    confidence, needs_review, notes = apply_division_flag(
        division_source, confidence, needs_review, notes
    )
    confidence, needs_review, notes = apply_date_flag(
        date, confidence, needs_review, notes
    )
    return HigherUnitRow(
        division=division,
        date=date,
        corps=corps.strip(),
        army_assigned=army_asgd.strip(),
        army_attached=army_atchd.strip(),
        group_assigned=group_asgd.strip(),
        group_attached=group_atchd.strip(),
        confidence=confidence,
        needs_review=needs_review,
        notes=notes,
        source_file=source_file,
        division_source=division_source,
    )


def _parse_table(
    table: Tag, division: str, division_source: str, source_file: str
) -> List[HigherUnitRow]:
    """Parse one ASSIGNMENT AND ATTACHMENT table into rows."""
    rows: List[HigherUnitRow] = []
    for tr in table.find_all("tr"):
        cells = [clean_cell(c) for c in tr.find_all(["td", "th"])]
        if _is_header_row(cells):
            continue
        row = _row_from_cells(cells, division, division_source, source_file)
        if row is not None:
            rows.append(row)
    return rows


def parse_higher_units(markdown: str, source_file: str = "") -> HigherUnitParseResult:
    """Parse ASSIGNMENT AND ATTACHMENT (higher-command) rows from markdown.

    Walks the document for tables in the ASSIGNMENT AND ATTACHMENT section,
    tracking the current division, and emits one dated higher-command row per
    data row. Header rows are skipped.
    """
    result = HigherUnitParseResult(source_file=source_file)
    for division, source, table in iter_section_tables(markdown, SECTION_HIGHER_UNITS):
        result.rows.extend(_parse_table(table, division, source, source_file))
    return result


def parse_higher_units_file(path: Path) -> HigherUnitParseResult:
    """Parse ASSIGNMENT AND ATTACHMENT rows from a markdown file."""
    result = parse_higher_units(path.read_text(encoding="utf-8"), source_file=path.name)
    logger.info(
        "Parsed %s: %d higher-unit row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result
