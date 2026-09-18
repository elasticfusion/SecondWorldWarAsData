"""Parse COMMAND AND STAFF tables from Chandra OCR+AI markdown into rows.

Handles the real-world irregularities validated on the OOB markdown:

* Division identity is tracked from title/inline name lines in the CONTENT, not
  the filename (files bleed across divisions; e.g. a file may contain several).
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
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

from src.ingestion.oob_markdown.models import (
    CommandStaffParseResult,
    CommandStaffRow,
)

logger = logging.getLogger(__name__)

# Division attributed to a command-staff table that appears before any division
# title in the content. We capture such rows (rather than drop them) but flag
# them for review, because the source cannot tell us the division: the OOB
# markdown filenames are misaligned with content (e.g. 1st_infantry.md contains
# 14th/16th Armored data), and such tables often precede the first in-content
# title. Assigning a division here would be a guess; flagging is honest.
UNKNOWN_DIVISION = "(unknown)"

# Rank tokens (longest first so multi-word ranks match before single words).
# Includes common OCR-tolerant handling via case-insensitive match.
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

# A division name line, e.g. "100th Infantry Division", possibly with leading
# run-together text; we capture the trailing division phrase.
_DIVISION_LINE_RE = re.compile(
    r"(\d{1,3}(?:st|nd|rd|d|th)\s+"
    r"(?:Infantry|Armored|Airborne|French Armored)\s+Division)",
    re.IGNORECASE,
)

# A letter-spaced H2 title, e.g. "## 1 0 1 st A I R B O R N E D I V I S I O N".
_H2_TITLE_RE = re.compile(r"^##\s+(.+)$")

# Spaceless form of a letter-spaced H2 title, e.g. "101STAIRBORNEDIVISION".
_SPACELESS_DIVISION_RE = re.compile(
    r"(\d{1,3}(?:ST|ND|RD|D|TH)(?:INFANTRY|ARMORED|AIRBORNE|FRENCHARMORED)DIVISION)",
    re.IGNORECASE,
)

# Section markers (may run together with adjacent text, so we search, not match).
_CMD_STAFF_MARKER = re.compile(r"COMMAND AND STAFF", re.IGNORECASE)

# Characters that should not appear in a clean person name (OCR garble cue).
_NAME_GARBLE_RE = re.compile(r"[^A-Za-z0-9 .'\-]")


def _spaceless_division(text: str) -> Optional[str]:
    """Detect a division in a letter-spaced OCR title by removing all spaces.

    Letter-spaced titles like "1 0 1 st A I R B O R N E D I V I S I O N" lose
    word boundaries when naively collapsed, so we match against the fully
    despaced form and reconstruct a clean division name from the ordinal + type.
    """
    despaced = re.sub(r"\s+", "", text)
    match = _SPACELESS_DIVISION_RE.search(despaced)
    if not match:
        return None
    token = match.group(1)
    parts = re.match(
        r"(\d{1,3})(ST|ND|RD|D|TH)(INFANTRY|ARMORED|AIRBORNE|FRENCHARMORED)DIVISION",
        token,
        re.IGNORECASE,
    )
    if not parts:
        return None
    number, ordinal, kind = parts.group(1), parts.group(2).lower(), parts.group(3)
    return f"{number}{ordinal} {kind.title()} Division"


def _division_from_line(line: str) -> Optional[str]:
    """Return a normalized division name if ``line`` names one, else None."""
    stripped = line.strip()
    match = _H2_TITLE_RE.match(stripped)
    if match:
        despaced_div = _spaceless_division(match.group(1))
        if despaced_div:
            return despaced_div
    div = _DIVISION_LINE_RE.search(stripped)
    if div:
        return _normalize_division(div.group(1))
    return None


def _normalize_division(name: str) -> str:
    """Normalize whitespace/casing of a division name (e.g. '100th Infantry Division')."""
    cleaned = re.sub(r"\s+", " ", name).strip()
    parts = re.match(
        r"(\d{1,3})(st|nd|rd|d|th)\s+(Infantry|Armored|Airborne|French Armored)\s+Division",
        cleaned,
        re.IGNORECASE,
    )
    if not parts:
        return cleaned
    number, ordinal, kind = parts.group(1), parts.group(2).lower(), parts.group(3)
    return f"{number}{ordinal} {kind.title()} Division"


def _clean_cell(tag: Tag) -> str:
    """Return cell text with <br/> -> space and entities decoded, collapsed."""
    text = tag.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _split_rank_name(cell: str) -> Tuple[str, str, bool]:
    """Split a combined 'rank name' cell into (rank, name, acting).

    Acting markers are detected and stripped. Rank is matched against the known
    vocabulary (longest first); anything after the rank is the name. When no
    rank is recognized, rank is "" and the whole remainder is the name (flagged
    upstream).
    """
    acting = bool(_ACTING_RE.search(cell))
    text = _ACTING_RE.sub("", cell).strip()
    for rank in RANK_TOKENS:
        pattern = re.compile(rf"^{re.escape(rank)}\b", re.IGNORECASE)
        if pattern.match(text):
            name = text[len(rank) :].strip()
            return rank, name, acting
    return "", text, acting


def _assess_row(rank: str, name: str) -> Tuple[float, bool, str]:
    """Return (confidence, needs_review, notes) for a parsed row.

    Verification only: flags suspect cells, does not alter them.
    """
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


def _update_state_from_text(
    text: str, division: str, in_cmd_staff: bool
) -> Tuple[str, bool]:
    """Fold the lines of a text node into the (division, in_cmd_staff) state."""
    for line in text.splitlines():
        div = _division_from_line(line)
        if div:
            division = div
        if _CMD_STAFF_MARKER.search(line):
            in_cmd_staff = True
        elif _is_other_section(line):
            in_cmd_staff = False
    return division, in_cmd_staff


def _iter_command_staff_tables(markdown: str) -> List[Tuple[str, Tag]]:
    """Yield (division, table_tag) for each COMMAND AND STAFF table.

    Walks the document in order, tracking the current division from title/name
    lines and whether we are inside a COMMAND AND STAFF section, and associates
    each table encountered while in that section with the current division.
    """
    soup = BeautifulSoup(markdown, "html.parser")
    current_division = ""
    in_cmd_staff = False
    pairs: List[Tuple[str, Tag]] = []

    for element in soup.descendants:
        if isinstance(element, Tag):
            if element.name == "table" and in_cmd_staff:
                pairs.append((current_division or UNKNOWN_DIVISION, element))
            continue
        current_division, in_cmd_staff = _update_state_from_text(
            str(element), current_division, in_cmd_staff
        )
    return pairs


_OTHER_SECTIONS = re.compile(
    r"STATISTICS|COMPOSITION|ORGANIC UNITS|ATTACHMENTS|DETACHMENTS|"
    r"ASSIGNMENT AND ATTACHMENT|COMMAND POSTS|CAMPAIGNS",
    re.IGNORECASE,
)


def _is_other_section(line: str) -> bool:
    """True if a line marks a section other than command-and-staff."""
    return bool(_OTHER_SECTIONS.search(line))


def _apply_unknown_division_flag(
    division: str, confidence: float, needs_review: bool, notes: str
) -> Tuple[float, bool, str]:
    """Force a review flag when the division could not be determined."""
    if division != UNKNOWN_DIVISION:
        return confidence, needs_review, notes
    note_extra = "division unknown (no title before table)"
    combined = f"{notes}; {note_extra}" if notes else note_extra
    return min(confidence, 0.5), True, combined


def _parse_table_rows(
    division: str, table: Tag, source_file: str
) -> List[CommandStaffRow]:
    """Expand a command-staff table into flat rows.

    Position grouping: a first cell with text sets the current position; an
    empty first cell (or a rowspan continuation, which BeautifulSoup renders as
    the first cell only appearing on the group's first <tr>) inherits it.
    """
    rows: List[CommandStaffRow] = []
    current_position = ""
    for tr in table.find_all("tr"):
        cells = list(tr.find_all(["td", "th"]))
        if not cells:
            continue
        texts = [_clean_cell(c) for c in cells]
        # A header row (th) or a 3-col data row: position | date | rank+name.
        if all(c.name == "th" for c in cells):
            continue
        position, date, rank_name = _row_fields(texts)
        if position:
            current_position = position
        elif not current_position:
            # No position established yet and none on this row: skip stray row.
            continue
        rank, name, acting = _split_rank_name(rank_name)
        confidence, needs_review, notes = _assess_row(rank, name)
        confidence, needs_review, notes = _apply_unknown_division_flag(
            division, confidence, needs_review, notes
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
            )
        )
    return rows


def _row_fields(texts: List[str]) -> Tuple[str, str, str]:
    """Map a cell-text list to (position, date, rank_name).

    Handles the common 3-column shape and the 2-column continuation shape (when
    a rowspan means the position cell is absent on continuation rows).
    """
    if len(texts) >= 3:
        return texts[0], texts[1], texts[2]
    if len(texts) == 2:
        # Continuation row under a rowspan: date | rank+name.
        return "", texts[0], texts[1]
    if len(texts) == 1:
        return "", "", texts[0]
    return "", "", ""


def parse_command_staff(
    markdown: str, source_file: str = ""
) -> CommandStaffParseResult:
    """Parse all COMMAND AND STAFF rows from a markdown string."""
    result = CommandStaffParseResult(source_file=source_file)
    for division, table in _iter_command_staff_tables(markdown):
        result.rows.extend(_parse_table_rows(division, table, source_file))
    return result


def parse_command_staff_file(path: Path) -> CommandStaffParseResult:
    """Parse COMMAND AND STAFF rows from a markdown file."""
    markdown = path.read_text(encoding="utf-8")
    result = parse_command_staff(markdown, source_file=path.name)
    logger.info(
        "Parsed %s: %d command-staff row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result
