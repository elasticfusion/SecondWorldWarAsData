"""Parse ORGANIC UNITS tables from Chandra OCR+AI markdown into rows.

Organic units are 2-column tables of unit names (no dates) under an
``ORGANIC UNITS`` header (with ``Special Troops`` / ``<Div> Artillery``
sub-sections that share the same table shape). Cells may carry a leading
footnote glyph (``*``, ``#``, ``@``) whose meaning is defined in trailing
plain-text footnotes; the glyph is captured into ``notes``.

Verification, not correction: suspect values are flagged, never rewritten.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

from bs4 import Tag

from src.ingestion.oob_markdown._common import (
    SECTION_ORGANIC_UNITS,
    apply_division_flag,
    clean_cell,
    iter_section_tables,
    iter_section_text_blocks,
)
from src.ingestion.oob_markdown.models import (
    OrganicUnitParseResult,
    OrganicUnitRow,
)

logger = logging.getLogger(__name__)

_GLYPH_RE = re.compile(r"^\s*([*#@]+)\s*")
_GARBLE_RE = re.compile(r"[^A-Za-z0-9 ()'./\-&]")
# Two-column text form: "<unit> ..... <unit>" (dot-leader separates the columns).
_DOT_LEADER_SPLIT_RE = re.compile(r"\.{2,}")
# Text-form sub-headers within ORGANIC UNITS that are not units themselves
# (e.g. "29th Division Artillery", "Special Troops"). They introduce a
# single-column list; the header line itself must not be emitted as a unit.
_SUBHEADER_RE = re.compile(
    r"^(.*Division Artillery|Special Troops|Composition)\s*$",
    re.IGNORECASE,
)


def _split_glyph(cell: str) -> Tuple[str, str]:
    """Return (unit_name, glyph_notes) splitting a leading footnote glyph."""
    match = _GLYPH_RE.match(cell)
    if not match:
        return cell, ""
    return cell[match.end() :].strip(), match.group(1)


def _assess_unit(name: str) -> Tuple[float, bool, str]:
    """Verification only: flag empty/garbled unit names."""
    if not name:
        return 0.5, True, "empty unit name"
    if _GARBLE_RE.search(name):
        return 0.5, True, "unit name contains unexpected characters"
    return 0.95, False, ""


def _make_row(
    unit_text: str, division: str, division_source: str, source_file: str
) -> "OrganicUnitRow | None":
    """Build one OrganicUnitRow from a raw unit cell/line, or None if empty."""
    unit_name, glyph = _split_glyph(unit_text)
    if not unit_name:
        return None
    confidence, needs_review, notes = _assess_unit(unit_name)
    confidence, needs_review, notes = apply_division_flag(
        division_source, confidence, needs_review, notes
    )
    note_field = glyph if not notes else f"{glyph} {notes}".strip()
    return OrganicUnitRow(
        division=division,
        unit_name=unit_name,
        notes=note_field,
        confidence=confidence,
        needs_review=needs_review,
        source_file=source_file,
        division_source=division_source,
    )


def _parse_table_rows(
    division: str, division_source: str, table: Tag, source_file: str
) -> List[OrganicUnitRow]:
    """Expand a 2-column organic-units table into one row per non-empty cell."""
    rows: List[OrganicUnitRow] = []
    for tr in table.find_all("tr"):
        for cell in tr.find_all(["td", "th"]):
            text = clean_cell(cell)
            if not text:
                continue
            row = _make_row(text, division, division_source, source_file)
            if row is not None:
                rows.append(row)
    return rows


def _parse_text_blocks(markdown: str, source_file: str) -> List[OrganicUnitRow]:
    """Parse the plain-text ORGANIC UNITS form (dot-leader + single-column lists).

    Some divisions encode ORGANIC UNITS as text rather than an HTML table:
    two-column ``<unit> ..... <unit>`` lines, plus single-column lists under
    sub-headers (``<Div> Division Artillery``, ``Special Troops``). Sub-header
    lines are consumed (not emitted as units); every other non-empty token is a
    unit.
    """
    rows: List[OrganicUnitRow] = []
    for division, source, lines in iter_section_text_blocks(
        markdown, SECTION_ORGANIC_UNITS
    ):
        for line in lines:
            stripped = line.strip()
            if not stripped or _SUBHEADER_RE.match(stripped):
                continue
            parts = _DOT_LEADER_SPLIT_RE.split(stripped)
            for part in parts:
                row = _make_row(part.strip(), division, source, source_file)
                if row is not None:
                    rows.append(row)
    return rows


def parse_organic_units(markdown: str, source_file: str = "") -> OrganicUnitParseResult:
    """Parse all ORGANIC UNITS rows from a markdown string (table or text form).

    Prefers the HTML-table form; falls back to the plain-text form (dot-leader
    two-column lines plus single-column artillery/special-troops lists) only when
    no table rows are found, so a division encoded either way is captured.
    """
    result = OrganicUnitParseResult(source_file=source_file)
    for division, source, table in iter_section_tables(markdown, SECTION_ORGANIC_UNITS):
        result.rows.extend(_parse_table_rows(division, source, table, source_file))
    if not result.rows:
        result.rows.extend(_parse_text_blocks(markdown, source_file))
    return result


def parse_organic_units_file(path: Path) -> OrganicUnitParseResult:
    """Parse ORGANIC UNITS rows from a markdown file."""
    result = parse_organic_units(
        path.read_text(encoding="utf-8"), source_file=path.name
    )
    logger.info(
        "Parsed %s: %d organic-unit row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result
