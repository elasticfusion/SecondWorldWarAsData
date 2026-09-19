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

# How a row's division was determined (for auditability and review triage).
DIVISION_SOURCE_TITLE = "title"  # read from an in-content division title
DIVISION_SOURCE_INFERRED = "inferred_next_title"  # Signal 1 inference
DIVISION_SOURCE_RECON = "inferred_recon_troop"  # Signal 2: from the recon troop
DIVISION_SOURCE_UNKNOWN = "unknown"  # no signal; left (unknown)

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
# A division's organic reconnaissance troop embeds the division number, e.g.
# "79th Reconnaissance Troop" -> 79th Division. Infantry divisions field a
# "Reconnaissance Troop"; this recovers the division number when the OCR dropped
# the title heading entirely (Signal 2). Type is not encoded here, so callers
# treat it as Infantry (the troop form is infantry-specific; armored divisions
# field a "Cavalry Reconnaissance Squadron" instead).
_RECON_TROOP_RE = re.compile(
    r"\b(\d{1,3})(st|nd|rd|d|th)\s+Reconnaissance\s+Troop\b",
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


def apply_division_flag(
    division_source: str, confidence: float, needs_review: bool, notes: str
) -> Tuple[float, bool, str]:
    """Adjust confidence/review based on how the division was determined.

    * ``title``  -> unchanged (division read directly from the source).
    * ``inferred_next_title`` -> flag for review, cap confidence; inferred, not
      fabricated, so a reviewer should confirm.
    * ``inferred_recon_troop`` -> flag for review, cap confidence; recovered from
      the organic reconnaissance troop when no title existed.
    * ``unknown`` -> flag for review, cap confidence; no division signal at all.
    """
    if division_source == DIVISION_SOURCE_TITLE:
        return confidence, needs_review, notes
    if division_source == DIVISION_SOURCE_INFERRED:
        extra = "division inferred from next title in file (verify)"
        combined = f"{notes}; {extra}" if notes else extra
        return min(confidence, 0.6), True, combined
    if division_source == DIVISION_SOURCE_RECON:
        extra = "division inferred from reconnaissance troop (verify)"
        combined = f"{notes}; {extra}" if notes else extra
        return min(confidence, 0.6), True, combined
    extra = "division unknown (no title in file)"
    combined = f"{notes}; {extra}" if notes else extra
    return min(confidence, 0.5), True, combined


def apply_unknown_division_flag(
    division: str, confidence: float, needs_review: bool, notes: str
) -> Tuple[float, bool, str]:
    """Backward-compatible flag helper keyed on the division string.

    Prefer :func:`apply_division_flag` (keyed on division_source). Retained so
    existing callers keep working: an ``(unknown)`` division flags for review.
    """
    if division != UNKNOWN_DIVISION:
        return confidence, needs_review, notes
    extra = "division unknown (no title before table)"
    combined = f"{notes}; {extra}" if notes else extra
    return min(confidence, 0.5), True, combined


# Month abbreviations (and common OCR/full-name variants) -> month number.
# Includes 3-9 letter abbreviations and full names, since the corpus mixes
# "7 Nov 44" with spelled-out "1 June" (command posts).
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
# A "day month [year]" military date, e.g. "7 Nov 44", "15 Sep 1943", "3 Sep",
# "1 June". The month is 3-9 letters (abbrev or full name).
_MIL_DATE_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z]{3,9})\.?(?:\s+(\d{2,4}))?\s*$")
# The ETO / WWII plausibility window. US Army divisions in this volume were
# activated as early as 1940 and demobilized by 1946; a parsed date outside this
# is almost certainly an OCR error or a mis-split cell, so it is flagged (never
# rewritten).
DATE_WINDOW_MIN_YEAR = 1940
DATE_WINDOW_MAX_YEAR = 1946


def check_date_plausibility(value: str) -> Optional[str]:
    """Return a problem note if a military date looks implausible, else None.

    Verification only — the value is never rewritten. Handles the corpus date
    forms ("7 Nov 44", "15 Sep 1943", and the year-less "3 Sep" used by command
    posts). Rules, each a distinct, catchable OCR-error class:

    * unparseable as a day/month(/year) date -> problem (e.g. a unit fragment
      leaked into the date cell like "84th Div ..... 8 Feb 45");
    * a day outside 1..31 or an unknown month -> problem;
    * a year (when present) outside the ETO window 1940-1946 -> problem
      (e.g. "16 Dec 1945" is in-window, but "16 Dec 1955" or a mis-split
      "26 Mar 439" is not).

    A year-less date ("3 Sep") is plausible as far as it goes (the year is
    tracked separately for command posts), so only its day/month are checked.
    """
    if not value or not value.strip():
        return None
    match = _MIL_DATE_RE.match(value)
    if not match:
        return "date unparseable (unexpected format)"
    day = int(match.group(1))
    month = _MONTHS.get(match.group(2).lower())
    if not 1 <= day <= 31:
        return f"day {day} out of range"
    if month is None:
        return f"unknown month '{match.group(2)}'"
    if match.group(3) is not None:
        year = _normalize_year(match.group(3))
        if not DATE_WINDOW_MIN_YEAR <= year <= DATE_WINDOW_MAX_YEAR:
            return (
                f"year {year} outside ETO window "
                f"{DATE_WINDOW_MIN_YEAR}-{DATE_WINDOW_MAX_YEAR}"
            )
    return None


def _normalize_year(raw: str) -> int:
    """Expand a 2-digit OOB year to 19xx; leave a 4-digit year as-is.

    Two-digit years in this WWII corpus are always 19xx (``44`` -> 1944). A
    3-digit value (e.g. a mis-split ``439``) is returned as-is so the window
    check flags it.
    """
    if len(raw) == 2:
        return 1900 + int(raw)
    return int(raw)


def apply_date_flag(
    value: str, confidence: float, needs_review: bool, notes: str
) -> Tuple[float, bool, str]:
    """Fold a date-plausibility problem into a row's confidence/review/notes.

    If :func:`check_date_plausibility` finds a problem, cap confidence and flag
    for review with an explanatory note; otherwise pass the row through
    unchanged. Verification, not correction.
    """
    problem = check_date_plausibility(value)
    if problem is None:
        return confidence, needs_review, notes
    combined = f"{notes}; {problem}" if notes else problem
    return min(confidence, 0.5), True, combined


def first_division_in(markdown: str) -> Optional[str]:
    """Return the first division title that appears anywhere in the document.

    Signal 1 for division inference: a leading section table/block that precedes
    any division title belongs (verified on real files) to the FIRST title that
    appears later in the same file. Since the leading region is by definition
    before the first title, "first title in document" is that "next title".
    """
    for line in text_lines(markdown):
        div = division_from_line(line)
        if div:
            return div
    return None


def division_from_recon_troop(markdown: str) -> Optional[str]:
    """Infer an Infantry division from its reconnaissance troop (Signal 2).

    When a file has no division title anywhere (OCR dropped the heading), the
    organic ``Nth Reconnaissance Troop`` still names the division number. Returns
    a normalized ``"<n><ord> Infantry Division"`` (the troop form is
    infantry-specific), or None if no recon troop is present. Never fabricates a
    type: armored divisions use a Cavalry Reconnaissance Squadron and so are not
    matched here.
    """
    match = _RECON_TROOP_RE.search(markdown)
    if not match:
        return None
    number, ordinal = match.group(1), match.group(2).lower()
    return f"{number}{ordinal} Infantry Division"


def attribute_division(
    seen_division: str,
    inferred_division: Optional[str],
    recon_division: Optional[str] = None,
) -> Tuple[str, str]:
    """Resolve a block's division and its source.

    Returns (division, division_source), preferring the strongest signal:
      * a division read from a title before the block  -> ``title``
      * else the inferred next-title division, if any  -> ``inferred_next_title``
      * else the recon-troop division, if any          -> ``inferred_recon_troop``
      * else                                           -> UNKNOWN_DIVISION / ``unknown``

    Never overwrites a title-read division; never fabricates.
    """
    if seen_division:
        return seen_division, DIVISION_SOURCE_TITLE
    if inferred_division:
        return inferred_division, DIVISION_SOURCE_INFERRED
    if recon_division:
        return recon_division, DIVISION_SOURCE_RECON
    return UNKNOWN_DIVISION, DIVISION_SOURCE_UNKNOWN


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


def iter_section_tables(
    markdown: str, wanted_section: str
) -> List[Tuple[str, str, Tag]]:
    """Yield (division, division_source, table) for tables in ``wanted_section``.

    Walks the document tracking the current division and section. A table that
    appears before any division title is attributed to the first title in the
    document (Signal 1 inference), tagged ``inferred_next_title``; a table under
    a title keeps that title's division (``title``); if the document has no
    title at all the division is inferred from the reconnaissance troop
    (``inferred_recon_troop``), else ``(unknown)``.
    """
    soup = BeautifulSoup(markdown, "html.parser")
    inferred = first_division_in(markdown)
    recon = division_from_recon_troop(markdown)
    division = ""
    section: Optional[str] = None
    results: List[Tuple[str, str, Tag]] = []
    for element in soup.descendants:
        if isinstance(element, Tag):
            if element.name == "table" and section == wanted_section:
                div, source = attribute_division(division, inferred, recon)
                results.append((div, source, element))
            continue
        division, section = _update_state(str(element), division, section)
    return results


def text_lines(markdown: str) -> List[str]:
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
) -> List[Tuple[str, str, List[str]]]:
    """Yield (division, division_source, lines) for text runs in ``wanted_section``.

    Plain-text analogue of :func:`iter_section_tables`, with the same Signal 1
    next-title inference for blocks that precede any division title, and the same
    Signal 2 reconnaissance-troop fallback when the file has no title at all.
    """
    inferred = first_division_in(markdown)
    recon = division_from_recon_troop(markdown)
    blocks: List[Tuple[str, str, List[str]]] = []
    division = ""
    section: Optional[str] = None
    collected: List[str] = []
    block_division = ""

    def flush() -> None:
        if collected and section == wanted_section:
            div, source = attribute_division(block_division, inferred, recon)
            blocks.append((div, source, list(collected)))

    for line in text_lines(markdown):
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


# Maps a section *marker* present in a document to the parser output key(s) that
# should carry its rows. Some markers do not map 1:1 to a parser:
#   * COMPOSITION is a wrapper header; its rows come out under organic_units.
#   * DETACHMENTS rows are emitted by the attachments parser (kind="detached"),
#     so both ATTACHMENTS and DETACHMENTS markers expect the "attachments" key.
#   * CAMPAIGNS may have no plain-text marker yet still yield rows from the
#     statistics table's Campaigns column; handled in coverage as a soft case.
_MARKER_TO_OUTPUT_KEYS: Dict[str, Tuple[str, ...]] = {
    SECTION_COMMAND_STAFF: ("command_staff",),
    SECTION_STATISTICS: ("statistics",),
    SECTION_CAMPAIGNS: ("campaigns",),
    SECTION_COMPOSITION: ("organic_units",),
    SECTION_ORGANIC_UNITS: ("organic_units",),
    SECTION_ATTACHMENTS: ("attachments",),
    SECTION_DETACHMENTS: ("attachments",),
    SECTION_HIGHER_UNITS: ("higher_units",),
    SECTION_COMMAND_POSTS: ("command_posts",),
}


def coverage_gaps(
    markdown: str, parsed_keys: Dict[str, int]
) -> List[Tuple[str, Tuple[str, ...]]]:
    """Report section markers present in ``markdown`` that yielded no rows.

    ``parsed_keys`` maps parser output keys (``command_staff``, ``statistics``,
    ...) to their row counts for this document. A section marker whose expected
    output key(s) all produced zero rows is a coverage gap — the region exists in
    the source but nothing was extracted from it, i.e. a silently-skipped section
    (an unrecognized format, or a parser that does not yet cover it).

    Returns a list of ``(marker_section, expected_output_keys)`` for each gap,
    so a caller can surface exactly which sections were seen-but-unparsed. This
    turns a silent skip into a reported, reviewable fact.
    """
    present = section_counts(markdown)
    gaps: List[Tuple[str, Tuple[str, ...]]] = []
    for marker in present:
        expected = _MARKER_TO_OUTPUT_KEYS.get(marker)
        if not expected:
            continue
        if any(parsed_keys.get(key, 0) > 0 for key in expected):
            continue
        gaps.append((marker, expected))
    return gaps
