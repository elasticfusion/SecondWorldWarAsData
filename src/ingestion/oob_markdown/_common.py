"""Shared machinery for OOB markdown section parsers.

Section-agnostic helpers used by every per-section parser (command-staff,
campaigns, command-posts, and future increments): division tracking across a
heterogeneous document, cell cleanup, and a section-aware table walk.

A single OOB markdown document interleaves many section types in different
regions (COMMAND AND STAFF, STATISTICS, CAMPAIGNS, ORGANIC UNITS, ATTACHMENTS,
DETACHMENTS, ASSIGNMENT AND ATTACHMENT, COMMAND POSTS). The walk here tracks the
current division and the current section as it moves through the document so
each parser can pick out only its own regions. This module is also the intended
home of the future region-coverage record (see INGESTION_FRONT_END.md).

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag

# Division attributed to a table that appears before any division title in the
# content. Captured (not dropped) but flagged, because the source cannot tell us
# the division: OOB markdown filenames are misaligned with content (e.g.
# 1st_infantry.md contains 14th/16th Armored data) and such tables often precede
# the first in-content title. Assigning a division here would be a guess.
UNKNOWN_DIVISION = "(unknown)"

# Canonical section keys. The walk classifies each region as one of these.
SECTION_COMMAND_STAFF = "command_staff"
SECTION_STATISTICS = "statistics"
SECTION_CAMPAIGNS = "campaigns"
SECTION_COMPOSITION = "composition"
SECTION_ORGANIC_UNITS = "organic_units"
SECTION_ATTACHMENTS = "attachments"
SECTION_DETACHMENTS = "detachments"
SECTION_HIGHER_UNITS = "higher_units"
SECTION_COMMAND_POSTS = "command_posts"

# Section marker patterns, searched (not matched) because OCR runs headers
# together with adjacent text. Order matters: more specific before less.
_SECTION_MARKERS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (SECTION_COMMAND_STAFF, re.compile(r"COMMAND AND STAFF", re.IGNORECASE)),
    (SECTION_HIGHER_UNITS, re.compile(r"ASSIGNMENT AND ATTACHMENT", re.IGNORECASE)),
    (SECTION_COMMAND_POSTS, re.compile(r"COMMAND POSTS", re.IGNORECASE)),
    (SECTION_ORGANIC_UNITS, re.compile(r"ORGANIC UNITS", re.IGNORECASE)),
    (SECTION_ATTACHMENTS, re.compile(r"ATTACHMENTS", re.IGNORECASE)),
    (SECTION_DETACHMENTS, re.compile(r"DETACHMENTS", re.IGNORECASE)),
    (SECTION_STATISTICS, re.compile(r"STATISTICS", re.IGNORECASE)),
    (
        SECTION_COMPOSITION,
        re.compile(r"COMPOSITION|C O M P O S I T I O N", re.IGNORECASE),
    ),
    (SECTION_CAMPAIGNS, re.compile(r"CAMPAIGNS", re.IGNORECASE)),
)

_DIVISION_LINE_RE = re.compile(
    r"(\d{1,3}(?:st|nd|rd|d|th)\s+"
    r"(?:Infantry|Armored|Airborne|French Armored)\s+Division)",
    re.IGNORECASE,
)
_H2_TITLE_RE = re.compile(r"^##\s+(.+)$")
_SPACELESS_DIVISION_RE = re.compile(
    r"(\d{1,3}(?:ST|ND|RD|D|TH)(?:INFANTRY|ARMORED|AIRBORNE|FRENCHARMORED)DIVISION)",
    re.IGNORECASE,
)
_DIVISION_PARTS_RE = re.compile(
    r"(\d{1,3})(st|nd|rd|d|th)\s+(Infantry|Armored|Airborne|French Armored)\s+Division",
    re.IGNORECASE,
)
_SPACELESS_PARTS_RE = re.compile(
    r"(\d{1,3})(ST|ND|RD|D|TH)(INFANTRY|ARMORED|AIRBORNE|FRENCHARMORED)DIVISION",
    re.IGNORECASE,
)


def normalize_division(name: str) -> str:
    """Normalize whitespace/casing of a division name."""
    cleaned = re.sub(r"\s+", " ", name).strip()
    parts = _DIVISION_PARTS_RE.match(cleaned)
    if not parts:
        return cleaned
    number, ordinal, kind = parts.group(1), parts.group(2).lower(), parts.group(3)
    return f"{number}{ordinal} {kind.title()} Division"


def _spaceless_division(text: str) -> Optional[str]:
    """Detect a division in a letter-spaced OCR title by removing all spaces."""
    match = _SPACELESS_DIVISION_RE.search(re.sub(r"\s+", "", text))
    if not match:
        return None
    parts = _SPACELESS_PARTS_RE.match(match.group(1))
    if not parts:
        return None
    number, ordinal, kind = parts.group(1), parts.group(2).lower(), parts.group(3)
    return f"{number}{ordinal} {kind.title()} Division"


def division_from_line(line: str) -> Optional[str]:
    """Return a normalized division name if ``line`` names one, else None."""
    stripped = line.strip()
    title = _H2_TITLE_RE.match(stripped)
    if title:
        despaced = _spaceless_division(title.group(1))
        if despaced:
            return despaced
    div = _DIVISION_LINE_RE.search(stripped)
    if div:
        return normalize_division(div.group(1))
    return None


def section_from_line(line: str) -> Optional[str]:
    """Return the section key a line marks, or None."""
    for key, pattern in _SECTION_MARKERS:
        if pattern.search(line):
            return key
    return None


def clean_cell(tag: Tag) -> str:
    """Return cell text with <br/> -> space and entities decoded, collapsed."""
    return re.sub(r"\s+", " ", tag.get_text(separator=" ")).strip()


def apply_unknown_division_flag(
    division: str, confidence: float, needs_review: bool, notes: str
) -> Tuple[float, bool, str]:
    """Force a review flag when the division could not be determined."""
    if division != UNKNOWN_DIVISION:
        return confidence, needs_review, notes
    extra = "division unknown (no title before table)"
    combined = f"{notes}; {extra}" if notes else extra
    return min(confidence, 0.5), True, combined


def _update_state(
    text: str, division: str, section: Optional[str]
) -> Tuple[str, Optional[str]]:
    """Fold a text node's lines into the (division, section) state."""
    for line in text.splitlines():
        div = division_from_line(line)
        if div:
            division = div
        sec = section_from_line(line)
        if sec:
            section = sec
    return division, section


def iter_section_tables(markdown: str, wanted_section: str) -> List[Tuple[str, Tag]]:
    """Yield (division, table) for tables inside ``wanted_section`` regions.

    Walks the document in order, tracking the current division and section, and
    returns each ``<table>`` encountered while the current section matches
    ``wanted_section``. Division defaults to :data:`UNKNOWN_DIVISION` when none
    has been seen yet.
    """
    soup = BeautifulSoup(markdown, "html.parser")
    division = ""
    section: Optional[str] = None
    pairs: List[Tuple[str, Tag]] = []
    for element in soup.descendants:
        if isinstance(element, Tag):
            if element.name == "table" and section == wanted_section:
                pairs.append((division or UNKNOWN_DIVISION, element))
            continue
        division, section = _update_state(str(element), division, section)
    return pairs


def _text_lines(markdown: str) -> List[str]:
    """Return the document's non-tag text as a flat list of lines, in order."""
    soup = BeautifulSoup(markdown, "html.parser")
    lines: List[str] = []
    for element in soup.descendants:
        if isinstance(element, Tag):
            continue
        lines.extend(str(element).splitlines())
    return lines


def iter_section_text_blocks(
    markdown: str, wanted_section: str
) -> List[Tuple[str, List[str]]]:
    """Yield (division, lines) for plain-text runs inside ``wanted_section``.

    For sections whose content is plain text rather than an HTML table (e.g. the
    plain-list CAMPAIGNS form). Single-pass state machine over the document's
    text lines: tracks the current division and section, and emits a block each
    time a wanted-section run ends (at a new section marker or end of input).
    """
    blocks: List[Tuple[str, List[str]]] = []
    division = ""
    section: Optional[str] = None
    collected: List[str] = []
    block_division = ""

    def flush() -> None:
        if collected and section == wanted_section:
            blocks.append((block_division or UNKNOWN_DIVISION, list(collected)))

    for line in _text_lines(markdown):
        div = division_from_line(line)
        if div:
            division = div
        sec = section_from_line(line)
        if sec is not None:
            flush()
            collected = []
            section = sec
            block_division = division
            continue
        if section == wanted_section and line.strip():
            if not collected:
                block_division = division
            collected.append(line.strip())
    flush()
    return blocks


def section_counts(markdown: str) -> Dict[str, int]:
    """Return a count of section markers seen (coverage-record precursor).

    A lightweight tally of which section regions appear in a document. This is
    the seed of the future per-document region-coverage record; parsers can
    compare parsed sections against this to report unparsed regions.
    """
    counts: Dict[str, int] = {}
    for line in markdown.splitlines():
        sec = section_from_line(line)
        if sec:
            counts[sec] = counts.get(sec, 0) + 1
    return counts
