"""Parse STATISTICS content from Chandra OCR+AI markdown into rows.

Statistics appear as plain text within a ``STATISTICS`` section: category
sub-headers (``Chronology``, ``Casualties (Tentative)``, ``Individual Awards``)
followed by ``Metric..... value`` dot-leader lines. (``Campaigns`` is a sibling
sub-block handled by the campaigns parser, not here.)

Verification, not correction: suspect values are flagged, never rewritten.

See docs/current/dataquality/INGESTION_FRONT_END.md ("Scanned documents").
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from src.ingestion.oob_markdown._common import (
    SECTION_STATISTICS,
    apply_unknown_division_flag,
    division_from_line,
    section_from_line,
    text_lines,
)
from src.ingestion.oob_markdown.models import (
    StatisticParseResult,
    StatisticRow,
)

logger = logging.getLogger(__name__)

# Recognized statistics category sub-headers (normalized keys).
_CATEGORIES = {
    "chronology": "Chronology",
    "casualties": "Casualties",
    "casualties (tentative)": "Casualties",
    "individual awards": "Individual Awards",
    "composition": "Composition",
}

# "Metric ..... value" — dot-leader separates a metric from its value. The
# leader is one-or-more dots (OCR sometimes yields ".." or ".....").
_DOT_LEADER_RE = re.compile(r"^(.*?)\.{2,}\s*(.*)$")

_GARBLE_RE = re.compile(r"[^A-Za-z0-9 ,()/#'.\-]")


def _category_of(line: str) -> Optional[str]:
    """Return the canonical category if ``line`` is a category sub-header."""
    return _CATEGORIES.get(line.strip().lower())


def _assess_stat(metric: str, value: str) -> Tuple[float, bool, str]:
    """Verification only: flag empty/garbled metric-value pairs."""
    problems: List[str] = []
    if not metric:
        problems.append("empty metric")
    if not value:
        problems.append("empty value")
    if value and _GARBLE_RE.search(value):
        problems.append("value contains unexpected characters")
    if problems:
        return 0.5, True, "; ".join(problems)
    return 0.95, False, ""


def _parse_stat_line(
    division: str, category: str, line: str, source_file: str
) -> Optional[StatisticRow]:
    """Parse one 'metric..... value' line into a row, or None if not a stat."""
    match = _DOT_LEADER_RE.match(line)
    if not match:
        return None
    metric = match.group(1).strip()
    value = match.group(2).strip()
    if not metric:
        return None
    confidence, needs_review, notes = _assess_stat(metric, value)
    confidence, needs_review, notes = apply_unknown_division_flag(
        division, confidence, needs_review, notes
    )
    return StatisticRow(
        division=division,
        category=category,
        metric=metric,
        value=value,
        confidence=confidence,
        needs_review=needs_review,
        notes=notes,
        source_file=source_file,
    )


def parse_statistics(markdown: str, source_file: str = "") -> StatisticParseResult:
    """Parse all STATISTICS metric/value rows from a markdown string.

    Walks the document tracking the current division and section. Within a
    STATISTICS region, category sub-headers (Chronology/Casualties/Individual
    Awards) group the metric/value lines. ``Campaigns`` is a nested sibling
    sub-block (parsed elsewhere), so it clears the category without ending the
    statistics region; the region ends when a different top-level section
    begins.
    """
    result = StatisticParseResult(source_file=source_file)
    division = ""
    in_stats = False
    category = ""
    for line in text_lines(markdown):
        div = division_from_line(line)
        if div:
            division = div
        in_stats, category = _advance_section(line, in_stats, category)
        if not in_stats or not category:
            continue
        row = _parse_stat_line(division, category, line, source_file)
        if row is not None:
            result.rows.append(row)
    return result


def _advance_section(line: str, in_stats: bool, category: str) -> Tuple[bool, str]:
    """Update (in_statistics, category) state for one line.

    Entering STATISTICS turns membership on; a different section turns it off.
    ``Campaigns`` and category sub-headers adjust the category without leaving
    statistics.
    """
    stripped = line.strip()
    sec = section_from_line(stripped)
    if sec == SECTION_STATISTICS:
        return True, ""
    if sec == "campaigns":
        # Nested sibling sub-block: clear category, stay in statistics.
        return in_stats, ""
    if sec is not None:
        # A different real section ends the statistics region.
        return False, ""
    cat = _category_of(stripped)
    if cat:
        return in_stats, cat
    return in_stats, category


def parse_statistics_file(path: Path) -> StatisticParseResult:
    """Parse STATISTICS rows from a markdown file."""
    result = parse_statistics(path.read_text(encoding="utf-8"), source_file=path.name)
    logger.info(
        "Parsed %s: %d statistic row(s), %d flagged for review",
        path.name,
        len(result.rows),
        result.review_count,
    )
    return result
