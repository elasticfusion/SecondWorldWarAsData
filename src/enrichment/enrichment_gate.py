"""Reusable gate for Grokipedia/Wikipedia enrichment checks — for ANY entity.

Implements three rules (owner-specified):

1. **Stamp every check.** Whenever a Grokipedia/Wikipedia enrichment check runs for a
   record, record WHEN it happened (``enrichment_checked_at`` epoch + ``_last_updated``
   date). This applies to all such checks, not just equipment.

2. **Diff the revised entry.** When a check produces data, compare it against what the
   record already holds; report whether anything actually changed, so callers can skip
   no-op writes and record *what* changed.

3. **Limit updates.** Skip the check entirely when the record was checked within the
   staleness window (default 90 days) — mirroring the OpenSERP ``searched_at`` gate — so
   we don't re-call Grokipedia/Wikipedia or re-write unchanged content.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List

#: default re-check window — matches the OpenSERP gate (90 days)
DEFAULT_RECHECK_SECONDS = 90 * 86400

_CHECKED_AT = "enrichment_checked_at"


def should_re_search(data: Dict[str, Any]) -> bool:
    """Shared staleness gate for date-stamped enrichment (people/people_groups/places).

    A ``not_found`` entity is re-searched once its ``last_enrichment_search`` date is older
    than ``enrichment.re_search_after_days`` (default 90). Never searched / unparseable ->
    eligible. Consolidates the previously-duplicated per-module ``_should_re_search``.
    """
    from datetime import datetime as _dt

    from src.utils.config import load_config

    days = load_config().get("enrichment", {}).get("re_search_after_days", 90)
    last_search = data.get("last_enrichment_search")
    if not last_search:
        return True
    try:
        return (_dt.now() - _dt.strptime(last_search, "%Y-%m-%d")).days >= days
    except (ValueError, TypeError):
        return True


def should_check_enrichment(
    record: Dict[str, Any], recheck_seconds: int = DEFAULT_RECHECK_SECONDS
) -> bool:
    """Return True if an enrichment check should run now (LIMIT UPDATES).

    Skips when the record was already enrichment-checked within the window. A record that
    has never been checked (no stamp) is always eligible.
    """
    checked_at = record.get(_CHECKED_AT, 0)
    if not checked_at:
        return True
    return (time.time() - checked_at) >= recheck_seconds


def stamp_checked(record: Dict[str, Any]) -> None:
    """Record that a Grokipedia/Wikipedia check happened now (applies to ALL checks)."""
    record[_CHECKED_AT] = int(time.time())
    record["_last_updated"] = datetime.now(timezone.utc).date().isoformat()


def diff_enrichment(existing: Dict[str, Any], incoming: Dict[str, Any]) -> List[str]:
    """Evaluate the diff of a revised entry: return the keys in ``incoming`` whose value
    is new or changed vs ``existing`` (gap-fill + real changes). Empty list == no change,
    so the caller can skip the write/re-stamp (LIMIT UPDATES)."""
    changed: List[str] = []
    for key, new_val in incoming.items():
        if new_val in (None, "", [], {}):
            continue  # nothing asserted for this key
        if existing.get(key) != new_val:
            changed.append(key)
    return changed


def apply_enrichment_diff(
    existing: Dict[str, Any], incoming: Dict[str, Any]
) -> List[str]:
    """Apply only the changed keys from ``incoming`` onto ``existing`` and stamp the
    check. Returns the list of changed keys (empty == no-op, but the check is still
    stamped so the staleness gate advances). Combines rules 1–3."""
    changed = diff_enrichment(existing, incoming)
    for key in changed:
        existing[key] = incoming[key]
    stamp_checked(existing)
    return changed
