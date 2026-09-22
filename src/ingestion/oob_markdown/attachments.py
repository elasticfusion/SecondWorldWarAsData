"""Parse ATTACHMENTS and DETACHMENTS content from Chandra OCR+AI markdown.

Both sections list support units and a period, grouped under arm-of-service
sub-headers (``Antiaircraft Artillery``, ``Armored``, ``Cavalry``, ``Engineer``,
``Field Artillery``, ``Infantry``, ``Tank Destroyer``, ...). ``ATTACHMENTS``
lists units attached *to* this division; ``DETACHMENTS`` lists this division's
units serving elsewhere (with the formation they were attached *to*).

The real Chandra corpus renders these as HTML ``<table>`` blocks, and the layout
varies between files, so this parser handles all observed shapes:

* **Arm sub-headers** appear either as a full-width ``<tr><td colspan=..><u>Arm
  </u></td></tr>`` row *inside* the table, OR as a plain-text ``Arm`` line
  *between* tables (sometimes inline in a ``(Contd)`` header). Both set the
  current arm; :func:`iter_section_elements` preserves their document order
  relative to the tables.
* **ATTACHMENTS data rows**: ``unit / start / end`` where the date may be one
  cell (``15 Nov 44 -``) or split across cells (``15 Nov 44 | - | 12 May 45``).
* **DETACHMENTS data rows** carry an extra "attached-to" column:
  ``unit / attached-to / date(s)`` (3, or 4-5 cells when dates are split).

A row is classified attachment-shaped vs detachment-shaped by whether the second
non-separator cell looks like a date (attachment) or a unit/formation
(detachment) — robust to the differing column counts. Units wrapped across two
table rows (a unit cell with an empty remainder, continued on the next row) are
stitched back together.

Verification, not correction: suspect cells (empty unit, unparseable dates) are
flagged, never rewritten — the same posture as the other OOB parsers.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from bs4 import Tag

from src.ingestion.oob_markdown._common import (
    SECTION_ATTACHMENTS,
    SECTION_DETACHMENTS,
    apply_date_flag,
    apply_division_flag,
    clean_cell,
    iter_section_elements,
)
from src.ingestion.oob_markdown.models import (
    AttachmentParseResult,
    AttachmentRow,
)

logger = logging.getLogger(__name__)

KIND_ATTACHED = "attached"
KIND_DETACHED = "detached"

# Arm-of-service sub-headers that group unit rows. Matched case-insensitively.
_ARMS = (
    "Antiaircraft Artillery",
    "Armored",
    "Cavalry",
    "Chemical",
    "Engineer",
    "Field Artillery",
    "Infantry",
    "Tank Destroyer",
    "Medical",
    "Signal",
    "Quartermaster",
    "Ordnance",
    "Military Police",
    "Reconnaissance",
)
_ARM_LOOKUP = {a.lower(): a for a in _ARMS}
# Trailing "(Contd)" on a repeated arm header ("Armored (Contd)").
_CONTD_RE = re.compile(r"\s*\(contd\)\s*$", re.IGNORECASE)
# A "(Contd)" section header may run the arm inline: "ATTACHMENTS (Contd)Cavalry
# (Contd)" — pull a known arm out of such a line.
_INLINE_ARM_RE = re.compile(
    r"([A-Za-z][A-Za-z ]+?)(?:\s*\(contd\))?\s*$", re.IGNORECASE
)
# A trailing dot-leader inside a unit cell ("398th AAA Av Bn (SP).....").
_DOT_LEADER_TAIL = re.compile(r"\.{2,}\s*$")
# A plausible OOB date fragment ("7 Nov 44", "15 Sep 1943").
_DATE_RE = re.compile(r"\d{1,2}\s+[A-Za-z]{3,}\.?\s+\d{2,4}")
# A bare range separator cell.
_SEP_RE = re.compile(r"^[-\u2013]$")


def _arm_of(line: str) -> Optional[str]:
    """Return the canonical arm name if ``line`` is (or ends with) an arm header."""
    stripped = _CONTD_RE.sub("", line.strip())
    direct = _ARM_LOOKUP.get(stripped.lower())
    if direct is not None:
        return direct
    # Inline form inside a "(Contd)" header: take the trailing arm if known.
    match = _INLINE_ARM_RE.search(stripped)
    if match:
        return _ARM_LOOKUP.get(match.group(1).strip().lower())
    return None


def _strip_leader(text: str) -> str:
    """Remove a trailing dot-leader from a unit cell."""
    return _DOT_LEADER_TAIL.sub("", text).strip()


def _is_date(text: str) -> bool:
    """True when a cell looks like an OOB date fragment."""
    return bool(_DATE_RE.search(text))


def _row_cells(tr: Tag) -> List[str]:
    """Cleaned, non-empty cell texts for a table row (drops bare separators)."""
    cells = [clean_cell(c) for c in tr.find_all(["td", "th"])]
    return [c for c in cells if c and not _SEP_RE.match(c)]


def _arm_header_in_row(tr: Tag) -> Optional[str]:
    """Return the arm if this row is an in-table ``<u>`` / colspan sub-header."""
    cells = tr.find_all(["td", "th"])
    if len(cells) != 1:
        return None
    return _arm_of(clean_cell(cells[0]))


@dataclass(frozen=True)
class _RowContext:
    """Provenance/grouping carried while parsing one unit row."""

    arm: str
    kind: str
    division: str
    division_source: str
    source_file: str


def _split_dates_from(cells: List[str]) -> Tuple[str, str]:
    """From trailing date cell(s) return (start, end).

    Handles a single combined cell ("15 Nov 44 -"), two cells ("15 Nov 44",
    "12 May 45"), or none.
    """
    dates = [c for c in cells if c]
    if not dates:
        return "", ""
    if len(dates) == 1:
        # One date cell: either "15 Nov 44 -" (open end), a bare "15 Nov 44",
        # or a combined range "12 Nov 44 - 2 Dec 44".
        part = dates[0].strip()
        halves = re.split(r"\s[-\u2013]\s", part, maxsplit=1)
        if len(halves) == 2 and _is_date(halves[1]):
            return halves[0].strip(), halves[1].strip()
        # Trailing dash = open end.
        if part.rstrip().endswith("-") or part.rstrip().endswith("\u2013"):
            return re.split(r"[-\u2013]", part, maxsplit=1)[0].strip(), ""
        return part, ""
    # Two cells: start may carry a trailing range dash ("15 Nov 44 -").
    start = re.split(r"[-\u2013]", dates[0], maxsplit=1)[0].strip()
    return start, dates[1].strip()


def _assess(unit: str, start: str, end: str) -> Tuple[float, bool, str]:
    """Verification only: flag empty units or non-date-looking date cells."""
    problems: List[str] = []
    if not unit:
        problems.append("empty unit")
    for label, value in (("start", start), ("end", end)):
        if value and not _is_date(value):
            problems.append(f"{label} date unrecognized")
    if problems:
        return 0.5, True, "; ".join(problems)
    return 0.95, False, ""


def _build_row(
    unit: str, attached_to: str, start: str, end: str, ctx: "_RowContext"
) -> AttachmentRow:
    """Assemble an AttachmentRow with the standard flag chain applied."""
    confidence, needs_review, notes = _assess(unit, start, end)
    confidence, needs_review, notes = apply_division_flag(
        ctx.division_source, confidence, needs_review, notes
    )
    confidence, needs_review, notes = apply_date_flag(
        start, confidence, needs_review, notes
    )
    confidence, needs_review, notes = apply_date_flag(
        end, confidence, needs_review, notes
    )
    return AttachmentRow(
        division=ctx.division,
        arm=ctx.arm,
        unit=unit,
        attached_to=attached_to,
        start_date=start,
        end_date=end,
        kind=ctx.kind,
        confidence=confidence,
        needs_review=needs_review,
        notes=notes,
        source_file=ctx.source_file,
        division_source=ctx.division_source,
    )


def _parse_data_row(cells: List[str], ctx: "_RowContext") -> Optional[AttachmentRow]:
    """Parse one non-header data row's cleaned cells into a row, or None.

    Shape is inferred from the cells: the first cell is always the unit. If the
    second cell looks like a date it is an ATTACHMENT (unit + dates); otherwise
    it is a DETACHMENT (unit + attached-to + dates). ``kind`` from the section
    still tags the row; the shape detection only decides how to read columns.
    """
    if not cells:
        return None
    unit = _strip_leader(cells[0])
    rest = cells[1:]
    if not unit:
        return None

    if rest and _is_date(rest[0]):
        # Attachment shape: unit + date(s).
        start, end = _split_dates_from(rest)
        attached_to = ""
    else:
        # Detachment shape: unit + attached-to + date(s).
        attached_to = _strip_leader(rest[0]) if rest else ""
        start, end = _split_dates_from(rest[1:])
    return _build_row(unit, attached_to, start, end, ctx)


def _parse_table(
    table: Tag,
    arm: str,
    kind: str,
    division: str,
    division_source: str,
    source_file: str,
) -> Tuple[List[AttachmentRow], str]:
    """Parse a table's rows; returns (rows, arm) with arm updated by in-table
    sub-headers so it carries to the next table."""
    rows: List[AttachmentRow] = []
    pending_unit = ""  # a unit cell whose row had no dates (wrapped continuation)
    for tr in table.find_all("tr"):
        header_arm = _arm_header_in_row(tr)
        if header_arm is not None:
            arm = header_arm
            continue
        cells = _row_cells(tr)
        if not cells:
            continue
        # Unit wrapped across rows: a lone unit cell with no dates continues on
        # the next row. Stitch by prefixing the pending fragment.
        if pending_unit:
            cells[0] = f"{pending_unit} {_strip_leader(cells[0])}".strip()
            pending_unit = ""
        if len(cells) == 1 and not _is_date(cells[0]):
            pending_unit = _strip_leader(cells[0])
            continue
        ctx = _RowContext(arm, kind, division, division_source, source_file)
        row = _parse_data_row(cells, ctx)
        if row is not None:
            rows.append(row)
    return rows, arm


def _parse_section(
    markdown: str, section: str, kind: str, source_file: str
) -> List[AttachmentRow]:
    """Parse one section (attachments or detachments) into rows.

    Walks the section's elements in document order (:func:`iter_section_elements`),
    tracking the current arm from both plain-text headers between tables and
    ``<u>`` sub-header rows inside tables.
    """
    rows: List[AttachmentRow] = []
    arm = ""
    for division, source, etype, payload in iter_section_elements(markdown, section):
        if etype == "text":
            branch = _arm_of(str(payload))
            if branch is not None:
                arm = branch
            continue
        if not isinstance(payload, Tag):  # defensive; tables are Tag payloads
            continue
        table_rows, arm = _parse_table(
            payload, arm, kind, division, source, source_file
        )
        rows.extend(table_rows)
    return rows


def parse_attachments(markdown: str, source_file: str = "") -> AttachmentParseResult:
    """Parse ATTACHMENTS and DETACHMENTS rows from markdown.

    Both sections are parsed; rows are tagged ``kind`` ("attached"/"detached")
    so the two are distinguishable in one result. Arm-of-service sub-headers
    group the unit rows under each arm.
    """
    result = AttachmentParseResult(source_file=source_file)
    result.rows.extend(
        _parse_section(markdown, SECTION_ATTACHMENTS, KIND_ATTACHED, source_file)
    )
    result.rows.extend(
        _parse_section(markdown, SECTION_DETACHMENTS, KIND_DETACHED, source_file)
    )
    return result


def parse_attachments_file(path: Path) -> AttachmentParseResult:
    """Parse ATTACHMENTS/DETACHMENTS rows from a markdown file."""
    result = parse_attachments(path.read_text(encoding="utf-8"), source_file=path.name)
    logger.info(
        "Parsed %s: %d attachment row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result
