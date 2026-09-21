"""Parse ATTACHMENTS and DETACHMENTS content from Chandra OCR+AI markdown.

Both sections share one shape: a branch sub-header (``Armored``, ``Cavalry``,
``Chemical``, ``Antiaircraft Artillery``, ``Field Artillery``, ``Engineer``,
``Infantry``, ...) followed by dot-leader lines of the form
``<unit>..... <start> - <end>`` (an attachment period), one per line. A unit may
carry a parenthetical parent (``Co A 47th Tk Bn (14th Armd Div)``) and the date
range may be open (a single date, or none).

``ATTACHMENTS`` lists units attached *to* this division; ``DETACHMENTS`` (often
"Assigned/attached elsewhere") lists this division's units serving elsewhere.
Rows are tagged with ``kind`` accordingly so both are captured without conflating
them.

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

from src.ingestion.oob_markdown._common import (
    SECTION_ATTACHMENTS,
    SECTION_DETACHMENTS,
    apply_date_flag,
    apply_division_flag,
    iter_section_text_blocks,
)
from src.ingestion.oob_markdown.models import (
    AttachmentParseResult,
    AttachmentRow,
)

logger = logging.getLogger(__name__)

KIND_ATTACHED = "attached"
KIND_DETACHED = "detached"

# Branch sub-headers that group attachment lines. Matched case-insensitively as
# a whole line (a header is a bare branch name, no dot-leader/date).
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
# A trailing "(Contd)" on a repeated branch header (e.g. "Armored (Contd)").
_CONTD_RE = re.compile(r"\s*\(contd\)\s*$", re.IGNORECASE)

# "<unit>..... <dates>" — dot-leader separates the unit from its date range.
_DOT_LEADER_RE = re.compile(r"^(.*?)\.{2,}\s*(.*)$")
# A date range "<start> - <end>" or a single "<start>"; dates are as-read text.
_RANGE_RE = re.compile(r"^(.*?)\s*[-–]\s*(.*)$")
# A plausible OOB date fragment (e.g. "7 Nov 44"): day month year-ish.
_DATE_RE = re.compile(r"\d{1,2}\s+[A-Za-z]{3,}\s+\d{2,4}")


def _arm_of(line: str) -> Optional[str]:
    """Return the canonical branch name if ``line`` is a branch sub-header."""
    stripped = _CONTD_RE.sub("", line.strip())
    return _ARM_LOOKUP.get(stripped.lower())


def _split_dates(raw: str) -> Tuple[str, str]:
    """Split a raw date field into (start, end); end is "" for a single date."""
    raw = raw.strip()
    if not raw:
        return "", ""
    match = _RANGE_RE.match(raw)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return raw, ""


def _assess(unit: str, start: str, end: str) -> Tuple[float, bool, str]:
    """Verification only: flag empty units or dates that don't look like dates."""
    problems: List[str] = []
    if not unit:
        problems.append("empty unit")
    for label, value in (("start", start), ("end", end)):
        if value and not _DATE_RE.search(value):
            problems.append(f"{label} date unrecognized")
    if problems:
        return 0.5, True, "; ".join(problems)
    return 0.95, False, ""


@dataclass(frozen=True)
class _LineContext:
    """Provenance/grouping carried while parsing one attachment line."""

    arm: str
    kind: str
    division: str
    division_source: str
    source_file: str


def _parse_line(line: str, ctx: "_LineContext") -> Optional[AttachmentRow]:
    """Parse one 'unit..... dates' attachment line into a row, or None."""
    match = _DOT_LEADER_RE.match(line)
    if not match:
        return None
    unit = match.group(1).strip()
    start, end = _split_dates(match.group(2))
    if not unit:
        return None
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
        start_date=start,
        end_date=end,
        kind=ctx.kind,
        confidence=confidence,
        needs_review=needs_review,
        notes=notes,
        source_file=ctx.source_file,
        division_source=ctx.division_source,
    )


def _parse_section(
    markdown: str, section: str, kind: str, source_file: str
) -> List[AttachmentRow]:
    """Parse one section (attachments or detachments) into rows.

    Within a section text block, a bare branch name sets the current arm; each
    subsequent dot-leader line is a unit attachment under that arm.
    """
    rows: List[AttachmentRow] = []
    for division, source, lines in iter_section_text_blocks(markdown, section):
        arm = ""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            branch = _arm_of(stripped)
            if branch is not None:
                arm = branch
                continue
            ctx = _LineContext(arm, kind, division, source, source_file)
            row = _parse_line(stripped, ctx)
            if row is not None:
                rows.append(row)
    return rows


def parse_attachments(markdown: str, source_file: str = "") -> AttachmentParseResult:
    """Parse ATTACHMENTS and DETACHMENTS rows from markdown.

    Both sections are parsed; rows are tagged ``kind`` ("attached"/"detached")
    so the two are distinguishable in one result. Branch sub-headers group the
    dot-leader unit lines under each arm.
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
