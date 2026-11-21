"""Public find-or-create entry points for places, dates and people groups, shared by Phase 2 and
Phase 3 (task 1.8).

The writers already exist inside the Phase 2 extractors (``places._find_or_create_place``,
``dates._find_or_create_date``, ``people_groups._save_group``), but they are private and expect the
caller to hold the extractor's in-memory index. A Phase 3 step that needs a record (a region a
place hierarchy names, a place a photo or a map shows, a unit or date a map shows) must create it
through the SAME writer, never a second one, so the record is identical whoever made it. These
functions are that entry point: each takes the entity's own directory, finds or creates the record
under the entity's ``index.json`` lock (the on-disk index is the authority, so two writers in
different processes converge on one record), and returns its file.

Their input comes from untrusted text (captions, Grok vision answers, place hierarchies), so a name
or date that is not one is refused (ValueError), and one process may create at most
``MAX_CREATED_PER_RUN`` records per entity type before further creations are refused, loudly.
"""

from __future__ import annotations

import copy
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

# a name longer than this (in UTF-8 bytes) is not an entity name (an LLM paragraph, a parse error)
MAX_NAME = 200
# records one process may CREATE per entity type through these entry points
MAX_CREATED_PER_RUN = 1000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# a date_start the dates extractor writes: ISO (partial) or a season / early-mid-late form
_DATE_START = re.compile(r"[0-9A-Za-z][0-9A-Za-z \-/]{0,39}")
_created: Dict[str, int] = {}
_created_lock = threading.Lock()


def _usable_name(name: Any) -> str:
    """The name as given (Phase 2 keys on ``name.lower()``, so it is not rewritten), refused when
    not text, empty, too long, with control characters, or starting with "_" (the index's own
    metadata keys)."""
    if not isinstance(name, str):
        raise ValueError("an entity name must be text")
    text = name.strip()
    if (
        not text
        or len(text.encode("utf-8")) > MAX_NAME
        or _CONTROL.search(text)
        or text.startswith("_")
    ):
        raise ValueError(f"not a usable entity name: {text[:60]!r}")
    return text


def _count_creation(entity: str, created: bool) -> None:
    """Count a record this process creates; past MAX_CREATED_PER_RUN refuse more, loudly."""
    if not created:
        return
    with _created_lock:
        n = _created.get(entity, 0) + 1
        _created[entity] = n
    if n == MAX_CREATED_PER_RUN + 1:
        from src.utils.observe import observe_error

        observe_error(
            "entity_writers",
            "creation_cap",
            note=(
                f"More than {MAX_CREATED_PER_RUN} new {entity} records were created through the "
                "shared writers in one run - far more than one document names. Further creations "
                "are refused; check which step is creating them (the task log) before re-running"
            ),
            entity=entity,
        )
    if n > MAX_CREATED_PER_RUN:
        raise ValueError(f"{entity} creation cap ({MAX_CREATED_PER_RUN}) reached")


def reset_creation_counts() -> None:
    with _created_lock:
        _created.clear()


def find_or_create_place(
    name: str, places_dir: Path, mention: Optional[Dict[str, Any]] = None
) -> Path:
    """The place record whose lower-cased name is a key of ``places/index.json``, created if
    absent. ``mention`` describes a new place: only ``geography_type``, ``source_language`` and
    in-range numeric ``latitude``/``longitude`` are used (untrusted input), and nothing of it is
    applied to an existing place. Provenance is the caller's: it attaches the event mention or
    image (with its SourceSectionID) that names the place. Raises ValueError for an unusable name
    or past the creation cap; RuntimeError when the schema guard refuses the new record.
    """
    from src.extraction.places import _find_or_create_place
    from src.utils.entity_records import load_json_index_keys

    clean = _usable_name(name)
    places_dir.mkdir(parents=True, exist_ok=True)
    known = clean.lower() in load_json_index_keys(places_dir / "index.json")
    _count_creation("places", not known)
    return _find_or_create_place(clean, _usable_place_mention(mention), places_dir, {})


def _short_text(v: Any, limit: int) -> bool:
    return isinstance(v, str) and 0 < len(v) <= limit and not _CONTROL.search(v)


def _number_in(v: Any, low: float, high: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high


def _usable_place_mention(mention: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The fields of an untrusted place mention the place writer may use for a new record."""
    m = mention if isinstance(mention, dict) else {}
    out: Dict[str, Any] = {
        k: m[k]
        for k in ("geography_type", "source_language")
        if _short_text(m.get(k), 40)
    }
    lat, lon = m.get("latitude"), m.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        if _number_in(lat, -90, 90) and _number_in(lon, -180, 180):
            out["latitude"], out["longitude"] = float(lat), float(lon)
    return out


def find_or_create_date(mention: Dict[str, Any], dates_dir: Path) -> Path:
    """The date record for ``mention`` (``date_start`` and optional ``time_start``, as the dates
    extractor reads them), created if absent. The find-vs-create decision is taken under the
    ``dates/index.json`` lock against the on-disk index and the new entry is saved before the lock
    is released (the Phase 2 extractor keeps its index in memory for one chapter; this entry point
    may run beside it). Raises ValueError for an unusable mention or past the creation cap;
    RuntimeError when the schema guard refuses the new record."""
    from src.extraction.dates import _find_or_create_date, _normalize_date_key
    from src.utils.file_lock import locked_json

    m = _usable_date_mention(mention)
    dates_dir.mkdir(parents=True, exist_ok=True)
    key = _normalize_date_key(m["date_start"], m.get("time_start"))
    with locked_json(dates_dir / "index.json") as (disk_index, save):
        existing = disk_index.get(key)
        if existing and (dates_dir / existing).exists():
            return dates_dir / existing
        _count_creation("dates", True)
        disk_index.pop(key, None)  # a stale entry for a file that is gone is recreated
        path = _find_or_create_date(m, dates_dir, disk_index)
        if not path.exists():  # the schema guard refused it: never index a missing file
            disk_index.pop(key, None)
            raise RuntimeError(f"date record for {m['date_start']!r} was refused")
        save(disk_index)
        return path


def _usable_date_mention(mention: Any) -> Dict[str, Any]:
    """A copy of ``mention`` checked for the fields that become the date's key and filename:
    ``date_start`` text in the extractor's shapes, ``time_start`` short text or absent, other text
    fields bounded. Raises ValueError otherwise."""
    if not isinstance(mention, dict):
        raise ValueError("a date mention must be an object")
    m = copy.deepcopy(mention)
    start = m.get("date_start")
    if not isinstance(start, str) or not _DATE_START.fullmatch(start.strip()):
        raise ValueError(f"not a usable date_start: {str(start)[:40]!r}")
    m["date_start"] = start.strip()
    if m.get("time_start") is not None and not _short_text(m["time_start"], 20):
        raise ValueError("time_start must be short text")
    return {k: v[:2000] if isinstance(v, str) else v for k, v in m.items()}


def find_or_create_group(
    group: Dict[str, Any],
    groups_dir: Path,
    book: str = "",
    author: str = "",
    series: str = "",
) -> Optional[Path]:
    """The people-group record for ``group`` (``group_name`` plus any fields the groups extractor
    writes), merged into the existing group of that name or created. ``book``/``author``/``series``
    are stamped on its event mentions, as Phase 2 does (on a copy: the caller's are untouched).
    Returns the group file, or None when the schema guard refused the write. Raises ValueError for
    an unusable group or past the creation cap."""
    from src.extraction.people_groups import _normalize_name, _save_group
    from src.utils.entity_records import load_json_index_keys

    if not isinstance(group, dict):
        raise ValueError("a group must be an object")
    record = copy.deepcopy(group)
    record["group_name"] = _usable_name(record.get("group_name"))
    mentions = record.get("event_mentions")
    record["event_mentions"] = [
        m
        for m in (mentions if isinstance(mentions, list) else [])
        if isinstance(m, dict)
    ]
    groups_dir.mkdir(parents=True, exist_ok=True)
    known = _normalize_name(record["group_name"]) in load_json_index_keys(
        groups_dir / "index.json"
    )
    _count_creation("people_groups", not known)
    return _save_group(
        groups_dir, groups_dir / "index.json", record, book, author, series
    )
