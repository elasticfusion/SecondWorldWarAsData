# Row-construction boilerplate (confidence/review/notes/source_file/division_source)
# NOTE: the per-row construction here is intentionally parallel to the other
# section parsers (campaigns/command-staff/etc.); pylint R0801 (duplicate-code)
# flags this, but abstracting it would couple otherwise-independent parsers for
# no real benefit. Score stays >= 9.9 per docs/current/core/DEVELOPMENT.md.
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
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, TypedDict

from bs4 import BeautifulSoup, Tag

from src.ingestion.oob_markdown._common import (
    SECTION_STATISTICS,
    apply_division_flag,
    attribute_division,
    clean_cell,
    division_from_line,
    division_from_recon_troop,
    first_division_in,
    section_from_line,
    text_lines,
)
from src.ingestion.oob_markdown.models import (
    StatisticParseResult,
    StatisticRow,
)

logger = logging.getLogger(__name__)


class _StatsWalkState(TypedDict):
    """Mutable state threaded through the STATISTICS table walk.

    Typed so ``division``/``category`` are known ``str`` (not ``object``) when
    passed to division/table helpers.
    """

    division: str
    in_stats: bool
    category: str


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


@dataclass(frozen=True)
class _TableContext:
    """Provenance carried while parsing a STATISTICS table into rows."""

    division: str
    division_source: str
    source_file: str


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
    division: str,
    division_source: str,
    category: str,
    line: str,
    source_file: str,
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
    confidence, needs_review, notes = apply_division_flag(
        division_source, confidence, needs_review, notes
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
        division_source=division_source,
    )


def parse_statistics(markdown: str, source_file: str = "") -> StatisticParseResult:
    """Parse all STATISTICS metric/value rows from a markdown string.

    Two encodings occur in the corpus and both are handled:

    * **Dot-leader text** — ``Metric..... value`` lines under category
      sub-headers (Chronology/Casualties/Individual Awards).
    * **HTML tables** — some divisions encode STATISTICS as a ``<table>`` whose
      header cells name categories and whose data rows carry metric/value cell
      pairs (single-column, or a two-column Casualties|Awards /
      Chronology|Campaigns layout). Without this, ~a third of divisions yielded
      empty-valued rows (values live in separate ``<td>`` cells the text walk
      never paired with their metric).

    Walks the document tracking the current division and section. ``Campaigns``
    is a nested sibling sub-block (parsed elsewhere), so it clears the category
    without ending the statistics region; the region ends when a different
    top-level section begins. Table rows are merged after the text pass and
    de-duplicated against it by (category, metric), so a file mixing both
    encodings is not double-counted.
    """
    result = StatisticParseResult(source_file=source_file)
    inferred = first_division_in(markdown)
    recon = division_from_recon_troop(markdown)
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
        resolved_division, source = attribute_division(division, inferred, recon)
        row = _parse_stat_line(resolved_division, source, category, line, source_file)
        if row is not None:
            result.rows.append(row)

    _merge_table_rows(markdown, source_file, result)
    return result


def _merge_table_rows(
    markdown: str, source_file: str, result: StatisticParseResult
) -> None:
    """Parse STATISTICS ``<table>`` rows and merge them into ``result``.

    In table-encoded STATISTICS the dot-leader text pass sees only the metric
    *labels* (values live in sibling ``<td>`` cells), so it emits empty-valued
    rows, often under the wrong category (the last header text it saw). When the
    table pass recovers a real value for the same *metric*, those text-pass
    shells are dropped; genuinely-empty dot-leader entries in pure-text files
    (no table row for the metric) are kept and stay flagged for review.
    """
    table_rows = _collect_statistics_tables(markdown, source_file)
    if not table_rows:
        return

    # Drop empty text-pass shells whose metric the table filled with a value.
    valued_metrics = {r.metric for r in table_rows if r.value.strip()}
    result.rows[:] = [
        r for r in result.rows if r.value.strip() or r.metric not in valued_metrics
    ]
    _upsert_rows(result.rows, table_rows)


def _upsert_rows(rows: List[StatisticRow], incoming: List[StatisticRow]) -> None:
    """Append ``incoming`` rows, replacing an empty-valued same-key row in place.

    Keyed by (category, metric): a new row for an unseen key is appended; for a
    seen key, it replaces the existing row only when the existing value is empty
    and the new one is not (so a recovered value wins over a blank).
    """
    seen = {(r.category, r.metric): idx for idx, r in enumerate(rows)}
    for row in incoming:
        key = (row.category, row.metric)
        idx = seen.get(key)
        if idx is None:
            seen[key] = len(rows)
            rows.append(row)
        elif not rows[idx].value.strip() and row.value.strip():
            rows[idx] = row


def _collect_statistics_tables(markdown: str, source_file: str) -> List[StatisticRow]:
    """Return rows from every ``<table>`` inside a STATISTICS region.

    Uses statistics-aware section tracking rather than the generic table walk:
    a ``Campaigns`` sub-header is a *nested* statistics sub-block (its tables are
    campaign lists, skipped here), so it must not end the region the way a
    generic section change would. Without this, tables after the campaigns
    sub-block (Casualties/Awards) would be missed.

    Category is also tracked from the *text* stream, because some divisions
    encode STATISTICS as single-column tables (metric/value only) whose category
    (Chronology/Casualties/Individual Awards) is a bare text header *between*
    tables rather than an in-table header row. That text-tracked category seeds
    each table's parse; in-table header rows still override it when present (the
    two-column layout).
    """
    soup = BeautifulSoup(markdown, "html.parser")
    inferred = first_division_in(markdown)
    recon = division_from_recon_troop(markdown)
    state: _StatsWalkState = {"division": "", "in_stats": False, "category": ""}
    rows: List[StatisticRow] = []
    for element in soup.descendants:
        if isinstance(element, Tag):
            if element.name == "table" and state["in_stats"]:
                resolved, source = attribute_division(
                    state["division"], inferred, recon
                )
                ctx = _TableContext(resolved, source, source_file)
                rows.extend(_parse_stat_table(element, ctx, state["category"]))
            continue
        _update_stats_walk_state(str(element), state)
    return rows


def _update_stats_walk_state(text: str, state: _StatsWalkState) -> None:
    """Fold a text node's lines into the statistics-walk ``state`` in place.

    Tracks the current ``division`` (title lines), STATISTICS ``in_stats``
    membership, and the current ``category`` (bare text sub-headers) used to seed
    single-column tables.
    """
    for line in text.splitlines():
        div = division_from_line(line)
        if div:
            state["division"] = div
        state["in_stats"] = _advance_stats_membership(line, state["in_stats"])
        cat = _category_of(line.strip())
        if cat:
            state["category"] = cat


def _advance_stats_membership(line: str, in_stats: bool) -> bool:
    """Track only STATISTICS-region membership for the table walk.

    Entering STATISTICS turns membership on. ``Campaigns`` is a nested sibling
    sub-block that stays inside statistics. Any other top-level section ends it.
    """
    sec = section_from_line(line.strip())
    if sec == SECTION_STATISTICS:
        return True
    if sec == "campaigns":
        return in_stats
    if sec is not None:
        return False
    return in_stats


def _parse_stat_table(
    table: Tag,
    ctx: _TableContext,
    seed_category: str = "",
) -> List[StatisticRow]:
    """Parse one STATISTICS ``<table>`` into rows, tracking category context.

    Header rows (cells naming a category, e.g. ``Casualties (Tentative)`` /
    ``Individual Awards``) switch the active left/right categories. Data rows
    carry one metric/value pair (single-column tables) or two pairs (the
    two-column casualties|awards / chronology|campaigns layout). ``Campaigns``
    values are not statistics and are skipped (handled by the campaigns parser).

    ``seed_category`` is the category tracked from surrounding text, used for
    single-column tables whose category header is a bare text line outside the
    table (an in-table header row still overrides it).
    """
    rows: List[StatisticRow] = []
    left_cat = seed_category
    right_cat = ""
    for tr in table.find_all("tr"):
        cells = [clean_cell(c) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c != ""]
        if not cells:
            continue
        header = _row_as_categories(cells)
        if header is not None:
            left_cat, right_cat = header
            continue
        rows.extend(_stat_pairs_from_row(cells, (left_cat, right_cat), ctx))
    return rows


def _row_as_categories(cells: List[str]) -> Optional[Tuple[str, str]]:
    """If every cell names a category, return (left_category, right_category).

    A header row switches category context; a data row does not. ``Campaigns``
    is recognized as a (right-side) category header so it clears the right
    column to a non-statistics context.
    """
    mapped = [
        _category_of(c) or ("Campaigns" if c.strip().lower() == "campaigns" else None)
        for c in cells
    ]
    if cells and all(m is not None for m in mapped):
        left = mapped[0] or ""
        right = mapped[1] if len(mapped) > 1 and mapped[1] else ""
        return left, right
    return None


def _stat_pairs_from_row(
    cells: List[str],
    categories: Tuple[str, str],
    ctx: _TableContext,
) -> List[StatisticRow]:
    """Build stat rows from a data row's metric/value cell pairs.

    Two cells -> one (left) pair; four cells -> a left pair and a right pair.
    Rows under a non-statistics right category (Campaigns) are skipped on that
    side. ``categories`` is ``(left_category, right_category)``.
    """
    left_cat, right_cat = categories
    out: List[StatisticRow] = []
    if len(cells) >= 2 and left_cat:
        row = _build_stat_row(left_cat, cells[0], cells[1], ctx)
        if row is not None:
            out.append(row)
    if len(cells) >= 4 and right_cat and right_cat != "Campaigns":
        row = _build_stat_row(right_cat, cells[2], cells[3], ctx)
        if row is not None:
            out.append(row)
    return out


def _build_stat_row(
    category: str, raw_metric: str, raw_value: str, ctx: _TableContext
) -> Optional[StatisticRow]:
    """Build one StatisticRow from a metric/value cell pair, or None if empty.

    Strips dot-leaders from the metric label and flags suspect values via the
    same assessment the text path uses.
    """
    metric = _DOT_LEADER_RE.sub(r"\1", raw_metric).strip().rstrip(".").strip()
    if not metric:
        return None
    value = raw_value.strip()
    confidence, needs_review, notes = _assess_stat(metric, value)
    confidence, needs_review, notes = apply_division_flag(
        ctx.division_source, confidence, needs_review, notes
    )
    return StatisticRow(
        division=ctx.division,
        category=category,
        metric=metric,
        value=value,
        confidence=confidence,
        needs_review=needs_review,
        notes=notes,
        source_file=ctx.source_file,
        division_source=ctx.division_source,
    )


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
