"""Parse COMMAND POSTS tables from Chandra OCR+AI markdown into rows.

Command-posts tables are ``DATE | TOWN | REGION | COUNTRY`` (with a ``<thead>``).
The year is carried in an underlined context, e.g. a DATE cell ``<u>1944</u>
<br/>5 Apr``; subsequent rows give only day-month (``30 Apr``) and inherit the
most recent year until a new ``<u>YYYY</u>`` appears. REGION may be empty and
COUNTRY may be abbreviated (as read).

Verification, not correction: suspect cells are flagged, never rewritten.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from bs4 import Tag

from src.ingestion.oob_markdown._common import (
    SECTION_COMMAND_POSTS,
    apply_unknown_division_flag,
    clean_cell,
    iter_section_tables,
)
from src.ingestion.oob_markdown.models import (
    CommandPostParseResult,
    CommandPostRow,
)

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"\b(19\d{2})\b")
_DAY_MONTH_RE = re.compile(
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
    re.IGNORECASE,
)


def _extract_year_and_date(date_cell: str) -> Tuple[Optional[str], str]:
    """Return (year_or_None, day_month) from a DATE cell.

    A year present in the cell (e.g. "1944 5 Apr") is pulled out and returned so
    the caller can update the inherited year; the remaining day-month text is
    returned as the date.
    """
    year_match = _YEAR_RE.search(date_cell)
    year = year_match.group(1) if year_match else None
    remainder = _YEAR_RE.sub("", date_cell).strip() if year else date_cell
    return year, remainder.strip()


def _assess_post(date: str, town: str, year: str) -> Tuple[float, bool, str]:
    """Verification only: flag suspect command-post cells."""
    problems: List[str] = []
    if not year:
        problems.append("no year established")
    if not town:
        problems.append("empty town")
    if date and not _DAY_MONTH_RE.search(date) and not _YEAR_RE.search(date):
        problems.append("date not day-month")
    if problems:
        return 0.5, True, "; ".join(problems)
    return 0.95, False, ""


def _cells(tr: Tag) -> List[str]:
    return [clean_cell(c) for c in tr.find_all(["td", "th"])]


def _is_header_row(tr: Tag) -> bool:
    cells = tr.find_all(["td", "th"])
    return bool(cells) and all(c.name == "th" for c in cells)


def _post_row_from_cells(
    texts: List[str], division: str, current_year: str, source_file: str
) -> Tuple[Optional[CommandPostRow], str]:
    """Build a command-post row from a cell-text list.

    Returns (row_or_None, updated_year). A year-only context row (no date/town)
    updates the year and yields no row.
    """
    date_raw = texts[0] if texts else ""
    town = texts[1] if len(texts) > 1 else ""
    region = texts[2] if len(texts) > 2 else ""
    country = texts[3] if len(texts) > 3 else ""

    year, date = _extract_year_and_date(date_raw)
    if year:
        current_year = year
    if not date and not town:
        return None, current_year

    confidence, needs_review, notes = _assess_post(date, town, current_year)
    confidence, needs_review, notes = apply_unknown_division_flag(
        division, confidence, needs_review, notes
    )
    row = CommandPostRow(
        division=division,
        date=date,
        year=current_year,
        town=town,
        region=region,
        country=country,
        confidence=confidence,
        needs_review=needs_review,
        notes=notes,
        source_file=source_file,
    )
    return row, current_year


def _parse_table_rows(
    division: str, table: Tag, source_file: str
) -> List[CommandPostRow]:
    """Expand a command-posts table into rows, inheriting the year."""
    rows: List[CommandPostRow] = []
    current_year = ""
    for tr in table.find_all("tr"):
        if _is_header_row(tr):
            continue
        texts = _cells(tr)
        if not texts:
            continue
        row, current_year = _post_row_from_cells(
            texts, division, current_year, source_file
        )
        if row is not None:
            rows.append(row)
    return rows


def parse_command_posts(markdown: str, source_file: str = "") -> CommandPostParseResult:
    """Parse all COMMAND POSTS rows from a markdown string."""
    result = CommandPostParseResult(source_file=source_file)
    for division, table in iter_section_tables(markdown, SECTION_COMMAND_POSTS):
        result.rows.extend(_parse_table_rows(division, table, source_file))
    return result


def parse_command_posts_file(path: Path) -> CommandPostParseResult:
    """Parse COMMAND POSTS rows from a markdown file."""
    result = parse_command_posts(
        path.read_text(encoding="utf-8"), source_file=path.name
    )
    logger.info(
        "Parsed %s: %d command-post row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result
