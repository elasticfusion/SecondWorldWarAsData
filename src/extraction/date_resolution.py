"""Deterministic date-interval resolution.

The SOURCE is the sole authority for a date. This resolver performs **no disambiguation
and no guessing** — it only mechanically expands the precision the source already stated
into a sortable, always-ISO ``[resolved_earliest, resolved_latest]`` interval. A vague
source yields a WIDE interval (that is the honest answer); it never fabricates precision.
More precision comes only from better source documents, never from this code.

Inputs are the fields the dates extractor already produces:
  - ``date_start``: ISO (``YYYY-MM-DD`` / ``YYYY-MM`` / ``YYYY``) OR an approximate string
    (``early-1944-06``, ``summer-1944``);
  - ``date_end``: optional ISO end of a stated range;
  - ``date_precision``: the stated precision tag.

Returns ``(resolved_earliest, resolved_latest, resolution_method)`` as ISO ``YYYY-MM-DD``
strings (or ``(None, None, "unresolved")`` when the stated value can't be mechanically
bounded — never guessed).

Config-driven: the month-third split and season bounds live in small tables below so they
can be tuned without touching logic. Season bounds are Northern-Hemisphere / European
(this corpus is the ETO).
"""

from __future__ import annotations

import calendar
import re
from typing import Optional, Tuple

# Northern-hemisphere / European meteorological-ish season bounds (month-day start/end).
_SEASON_BOUNDS = {
    "spring": ((3, 1), (5, 31)),
    "summer": ((6, 1), (8, 31)),
    "fall": ((9, 1), (11, 30)),
    "autumn": ((9, 1), (11, 30)),
    "winter": ((12, 1), (2, 28)),  # winter spans year end; handled specially below
}

# Early/mid/late split of a month into thirds (day-of-month ranges).
_MONTH_THIRD = {
    "early": (1, 10),
    "mid": (11, 20),
    "late": (21, None),  # None -> last day of month
}

_APPROX_PREFIXES = (
    "early",
    "mid",
    "late",
    "spring",
    "summer",
    "fall",
    "autumn",
    "winter",
)

_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_ISO_YEAR = re.compile(r"^(\d{4})$")


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _iso(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def _resolve_exact(date_start: str) -> Optional[Tuple[str, str]]:
    """Exact ISO forms -> interval. YYYY-MM-DD -> point; YYYY-MM -> whole month; YYYY ->
    whole year. Returns None if not a plain ISO form."""
    m = _ISO_DAY.match(date_start)
    if m:
        return date_start, date_start
    m = _ISO_MONTH.match(date_start)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return _iso(y, mo, 1), _iso(y, mo, _last_day(y, mo))
    m = _ISO_YEAR.match(date_start)
    if m:
        y = int(m.group(1))
        return _iso(y, 1, 1), _iso(y, 12, 31)
    return None


def _split_approx(date_start: str) -> Optional[Tuple[str, str]]:
    """Split an approximate string 'prefix-rest' -> (prefix, rest). Tolerates the
    sortable-key order too ('1944-06-early')."""
    for pfx in _APPROX_PREFIXES:
        if date_start.startswith(pfx + "-"):
            return pfx, date_start[len(pfx) + 1 :]
        if date_start.endswith("-" + pfx):
            return pfx, date_start[: -(len(pfx) + 1)]
    return None


def _resolve_month_third(prefix: str, rest: str) -> Optional[Tuple[str, str]]:
    """early/mid/late of a YYYY-MM month -> the matching third of that month."""
    m = _ISO_MONTH.match(rest)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    lo, hi = _MONTH_THIRD[prefix]
    hi = hi or _last_day(y, mo)
    return _iso(y, mo, lo), _iso(y, mo, hi)


def _resolve_season(prefix: str, rest: str) -> Optional[Tuple[str, str]]:
    """spring/summer/fall/winter of a YYYY -> that season's bounds (N. hemisphere)."""
    m = _ISO_YEAR.match(rest)
    if not m:
        return None
    y = int(m.group(1))
    (sm, sd), (em, ed) = _SEASON_BOUNDS[prefix]
    if prefix == "winter":
        # Dec of the stated year through end of Feb the following year.
        return _iso(y, 12, 1), _iso(y + 1, 2, _last_day(y + 1, 2))
    return _iso(y, sm, sd), _iso(y, em, ed)


def resolve_date_interval(
    date_start: Optional[str],
    date_end: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], str]:
    """Expand a source-stated date into a sortable ISO interval. Never guesses.

    ``date_start`` is self-describing (its form encodes the precision: ``1944-06-06`` /
    ``1944-06`` / ``1944`` / ``early-1944-06`` / ``summer-1944``), so no separate precision
    arg is needed. Returns (resolved_earliest, resolved_latest, resolution_method):
      - "precision_rule": derived mechanically from the stated form;
      - "range": a stated start..end range (both ISO);
      - "unresolved": could not be mechanically bounded (resolved_* are None).
    """
    if not date_start:
        return None, None, "unresolved"
    ds = date_start.strip().lower()

    # A stated range (date_start..date_end), both plain ISO -> span the two.
    if date_end:
        start = _resolve_exact(ds)
        end = _resolve_exact(date_end.strip().lower())
        if start and end:
            return start[0], end[1], "range"

    # Exact ISO forms (day / month / year).
    exact = _resolve_exact(ds)
    if exact:
        return exact[0], exact[1], "precision_rule"

    # Approximate forms (early/mid/late month, or season year).
    approx = _resolve_approximate(ds)
    if approx:
        return approx[0], approx[1], "precision_rule"

    # Could not mechanically bound it -> leave null (never guess).
    return None, None, "unresolved"


def _resolve_approximate(ds: str) -> Optional[Tuple[str, str]]:
    """Resolve an approximate 'prefix-...' form (month third or season year) or None."""
    split = _split_approx(ds)
    if not split:
        return None
    prefix, rest = split
    if prefix in _MONTH_THIRD:
        return _resolve_month_third(prefix, rest)
    if prefix in _SEASON_BOUNDS:
        return _resolve_season(prefix, rest)
    return None
