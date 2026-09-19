"""Parse COMMAND AND STAFF tables from Chandra OCR+AI markdown into rows.

Handles the real-world irregularities validated on the OOB markdown:

* Division identity is tracked from title/inline name lines in the CONTENT, not
  the filename (files bleed across divisions; e.g. a file may contain several).
  Shared division/section tracking lives in ``_common``.
* Position grouping appears two ways: ``rowspan="N"`` on the first cell, OR an
  empty first ``<td></td>`` meaning "same position as the row above".
* Rank and name are combined in one cell (e.g. "Maj Gen William C Lee");
  acting status appears inline as "(actg)"/"(Actg)".
* Cells contain ``<br/>`` and ``&amp;`` and OCR garble.

Verification, not correction: suspect cells are flagged, never rewritten.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

from bs4 import Tag

from src.ingestion.oob_markdown._common import (
    SECTION_COMMAND_STAFF,
    UNKNOWN_DIVISION,
    apply_division_flag,
    apply_date_flag,
    clean_cell,
    iter_section_tables,
)
from src.ingestion.oob_markdown.models import (
    CommandStaffParseResult,
    CommandStaffRow,
)

logger = logging.getLogger(__name__)

# Rank tokens (longest first so multi-word ranks match before single words).
RANK_TOKENS: Tuple[str, ...] = (
    "Maj Gen",
    "Brig Gen",
    "Lt Gen",
    "Lt Col",
    "1st Lt",
    "2d Lt",
    "Gen",
    "Col",
    "Maj",
    "Capt",
    "Lt",
    "WOJG",
)

_ACTING_RE = re.compile(r"\(\s*act?g\s*\)", re.IGNORECASE)

# Characters that should not appear in a clean person name (OCR garble cue).
_NAME_GARBLE_RE = re.compile(r"[^A-Za-z0-9 .'\-]")


def _split_rank_name(cell: str) -> Tuple[str, str, bool]:
    """Split a combined 'rank name' cell into (rank, name, acting)."""
    acting = bool(_ACTING_RE.search(cell))
    text = _ACTING_RE.sub("", cell).strip()
    for rank in RANK_TOKENS:
        pattern = re.compile(rf"^{re.escape(rank)}\b", re.IGNORECASE)
        if pattern.match(text):
            return rank, text[len(rank) :].strip(), acting
    return "", text, acting


def _assess_row(rank: str, name: str) -> Tuple[float, bool, str]:
    """Return (confidence, needs_review, notes). Verification only, no changes."""
    problems: List[str] = []
    if not name:
        problems.append("empty name")
    if not rank:
        problems.append("unrecognized rank")
    if name and _NAME_GARBLE_RE.search(name):
        problems.append("name contains unexpected characters")
    if problems:
        return 0.5, True, "; ".join(problems)
    return 0.95, False, ""


def _row_fields(texts: List[str]) -> Tuple[str, str, str]:
    """Map a cell-text list to (position, date, rank_name).

    Handles the 3-column shape and the 2-column continuation shape (a rowspan
    means the position cell is absent on continuation rows).
    """
    if len(texts) >= 3:
        return texts[0], texts[1], texts[2]
    if len(texts) == 2:
        return "", texts[0], texts[1]
    if len(texts) == 1:
        return "", "", texts[0]
    return "", "", ""


def _parse_table_rows(
    division: str, division_source: str, table: Tag, source_file: str
) -> List[CommandStaffRow]:
    """Expand a command-staff table into flat rows (rowspan/empty-td grouping)."""
    rows: List[CommandStaffRow] = []
    current_position = ""
    for tr in table.find_all("tr"):
        cells = list(tr.find_all(["td", "th"]))
        if not cells or all(c.name == "th" for c in cells):
            continue
        position, date, rank_name = _row_fields([clean_cell(c) for c in cells])
        if position:
            current_position = position
        elif not current_position:
            continue
        rank, name, acting = _split_rank_name(rank_name)
        confidence, needs_review, notes = _assess_row(rank, name)
        confidence, needs_review, notes = apply_division_flag(
            division_source, confidence, needs_review, notes
        )
        confidence, needs_review, notes = apply_date_flag(
            date, confidence, needs_review, notes
        )
        rows.append(
            CommandStaffRow(
                division=division,
                position=current_position,
                effective_date=date,
                rank=rank,
                name=name,
                acting=acting,
                confidence=confidence,
                needs_review=needs_review,
                notes=notes,
                source_file=source_file,
                raw_cell=rank_name,
                division_source=division_source,
            )
        )
    return rows


def parse_command_staff(
    markdown: str, source_file: str = ""
) -> CommandStaffParseResult:
    """Parse all COMMAND AND STAFF rows from a markdown string."""
    result = CommandStaffParseResult(source_file=source_file)
    for division, source, table in iter_section_tables(markdown, SECTION_COMMAND_STAFF):
        result.rows.extend(_parse_table_rows(division, source, table, source_file))
    return result


def parse_command_staff_file(path: Path) -> CommandStaffParseResult:
    """Parse COMMAND AND STAFF rows from a markdown file."""
    result = parse_command_staff(
        path.read_text(encoding="utf-8"), source_file=path.name
    )
    logger.info(
        "Parsed %s: %d command-staff row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result


# Re-exported for backward compatibility with existing imports/tests.
__all__ = [
    "RANK_TOKENS",
    "UNKNOWN_DIVISION",
    "parse_command_staff",
    "parse_command_staff_file",
]
