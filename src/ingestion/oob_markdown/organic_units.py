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
)
from src.ingestion.oob_markdown.models import (
    OrganicUnitParseResult,
    OrganicUnitRow,
)

logger = logging.getLogger(__name__)

_GLYPH_RE = re.compile(r"^\s*([*#@]+)\s*")
_GARBLE_RE = re.compile(r"[^A-Za-z0-9 ()'./\-&]")


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
            unit_name, glyph = _split_glyph(text)
            if not unit_name:
                continue
            confidence, needs_review, notes = _assess_unit(unit_name)
            confidence, needs_review, notes = apply_division_flag(
                division_source, confidence, needs_review, notes
            )
            note_field = glyph if not notes else f"{glyph} {notes}".strip()
            rows.append(
                OrganicUnitRow(
                    division=division,
                    unit_name=unit_name,
                    notes=note_field,
                    confidence=confidence,
                    needs_review=needs_review,
                    source_file=source_file,
                    division_source=division_source,
                )
            )
    return rows


def parse_organic_units(markdown: str, source_file: str = "") -> OrganicUnitParseResult:
    """Parse all ORGANIC UNITS rows from a markdown string."""
    result = OrganicUnitParseResult(source_file=source_file)
    for division, source, table in iter_section_tables(markdown, SECTION_ORGANIC_UNITS):
        result.rows.extend(_parse_table_rows(division, source, table, source_file))
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
