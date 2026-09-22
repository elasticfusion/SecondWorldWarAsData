"""Transform ``output/`` entity files into normalized rows for the DB schema.

Pure, DB-agnostic extract/transform layer for the schema in
``docs/SCHEMA_DESIGN.md``. Each ``to_*_rows`` reader turns a category's JSON
files into a list of flat ``dict`` rows (typed columns the site queries, plus a
``raw`` JSON blob preserving full fidelity). The loader
(:mod:`src.loader.load`) is a thin adapter that inserts these rows via any
DB-API connection (sqlite for tests, psycopg for Postgres) — so this module has
no database dependency and is fully unit-testable against the real files.

The model is event-centric: events/sub-events are the hub (from
``output/content``), entities carry ``<Entity>ID`` and link back through
``event_mentions[]`` (flattened into the ``mentions`` table).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Non-entity files present in entity dirs, to skip.
_SKIP_FILES = frozenset(
    {
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "not_related.json",
        "not_people.json",
        "review_queue.json",
        "related_groups_report.json",
        "geo_report.json",
        ".processed_events.json",
    }
)

# Per-run tally of files skipped during loading, keyed by reason. A load reads
# this to surface "skipped: N" alongside the loaded counts so a batch of
# corrupt/misshapen files that quietly didn't load is visible, not silent.
_SKIPS: Dict[str, int] = {}


def reset_skips() -> None:
    """Clear the skip tally (call at the start of a load)."""
    _SKIPS.clear()


def skip_count() -> int:
    """Total files skipped since the last :func:`reset_skips`."""
    return sum(_SKIPS.values())


def skip_breakdown() -> Dict[str, int]:
    """Copy of the per-reason skip tally."""
    return dict(_SKIPS)


def _record_skip(reason: str, path: Path, detail: Any = None) -> None:
    """Log a skipped file at WARNING and increment the per-reason tally."""
    _SKIPS[reason] = _SKIPS.get(reason, 0) + 1
    logger.warning("Skipped %s (%s): %s", path, reason, detail)


def _iter_entity_files(entity_dir: Path) -> Iterable[Dict[str, Any]]:
    """Yield parsed JSON dicts for each entity file in a directory.

    Files that fail to parse, aren't JSON objects, or can't be read are skipped
    to keep the load resilient — but each skip is logged at WARNING and counted
    (see :data:`_SKIPS`) so silent data loss is impossible to miss.
    """
    if not entity_dir.is_dir():
        return
    for path in sorted(entity_dir.glob("*.json")):
        if path.name in _SKIP_FILES:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _record_skip("unreadable_or_invalid_json", path, exc)
            continue
        if isinstance(data, dict):
            yield data
        else:
            _record_skip("not_a_json_object", path, f"top-level {type(data).__name__}")


def _json(value: Any) -> str:
    """Serialize a value to a compact JSON string for a ``raw`` column."""
    return json.dumps(value, ensure_ascii=False)


# --- events / sub-events (hub) ------------------------------------------


def to_event_rows(content_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Read ``output/content/<Book>/*-event.json`` into event + sub-event rows.

    Returns a dict with ``events`` and ``sub_events`` row lists (both come from
    the same files, so they are produced together).
    """
    events: List[Dict[str, Any]] = []
    sub_events: List[Dict[str, Any]] = []
    seen_events: set = set()
    for path in sorted(content_root.rglob("*-event.json")):
        parsed = _load_event_file(path)
        if parsed is None:
            continue
        event, book, chapter = parsed
        event_id = event.get("EventID")
        if not event_id:
            continue
        if event_id not in seen_events:
            seen_events.add(event_id)
            events.append(
                {
                    "event_id": event_id,
                    "event_name": event.get("Event_Name"),
                    "book": book,
                    "chapter": chapter,
                }
            )
        sub_events.extend(_sub_event_rows(event, event_id))
    return {"events": events, "sub_events": sub_events}


def _load_event_file(path: Path):
    """Return (event_dict, book, chapter) for an event file, or None.

    Skips (with a logged WARNING + tally) files that don't parse or lack a
    usable ``Event`` object, so a broken event file can't vanish silently.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _record_skip("event_unreadable_or_invalid_json", path, exc)
        return None
    event = data.get("Event")
    if isinstance(event, list):
        event = event[0] if event else None
    if not isinstance(event, dict):
        _record_skip("event_missing_or_malformed", path, "no Event object")
        return None
    return event, path.parent.name, data.get("Chapter")


def _sub_event_rows(event: Dict[str, Any], event_id: str) -> List[Dict[str, Any]]:
    """Build sub-event rows for one event."""
    rows: List[Dict[str, Any]] = []
    for sub in event.get("Sub-events", []) or []:
        sub_id = sub.get("Sub-eventID")
        if not sub_id:
            continue
        rows.append(
            {
                "sub_event_id": sub_id,
                "event_id": event_id,
                "summary": sub.get("Sub-event_summary"),
                "fulltext": _flatten_fulltext(sub.get("Sub-event_fulltext")),
                "endnote_refs": _json(sub.get("Endnote_References") or []),
                "footnote_refs": _json(sub.get("Footnote_References") or []),
            }
        )
    return rows


def _flatten_fulltext(fulltext: Any) -> Optional[str]:
    """Flatten a ``{Paragraph_1: ..., ...}`` fulltext object into one string."""
    if isinstance(fulltext, str):
        return fulltext
    if isinstance(fulltext, dict):
        return "\n\n".join(str(v) for v in fulltext.values() if v)
    return None


# --- generic entity + mentions ------------------------------------------


def to_entity_rows(
    entity_dir: Path, id_field: str, column_map: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Read an entity dir into rows: mapped typed columns + a ``raw`` blob.

    ``column_map`` maps output column name -> source JSON key. The primary key
    column is included via ``id_field`` mapped in ``column_map``.
    """
    rows: List[Dict[str, Any]] = []
    for data in _iter_entity_files(entity_dir):
        if not data.get(id_field):
            continue
        row = {col: data.get(src) for col, src in column_map.items()}
        row["raw"] = _json(data)
        rows.append(row)
    return rows


def to_mention_rows(entity_dir: Path, entity_type: str, id_field: str) -> List[dict]:
    """Flatten every entity's ``event_mentions[]`` into mention rows.

    Produces one row per (entity, mention), linking the entity to a sub-event
    and (when present) a source, with the verbatim reference for citation.
    """
    rows: List[Dict[str, Any]] = []
    for data in _iter_entity_files(entity_dir):
        entity_id = data.get(id_field)
        if not entity_id:
            continue
        for mention in data.get("event_mentions", []) or []:
            rows.append(
                {
                    # Coalesce to "" (never None): mention_id is part of the
                    # composite PK, and SQLite/Postgres treat NULL key parts as
                    # distinct, which would break idempotent reloads. The
                    # (mention_id, entity_type, entity_id, sub_event_id) tuple
                    # stays unique with "" standing in for a missing id.
                    "mention_id": mention.get("MentionID") or "",
                    "sub_event_id": mention.get("Sub_eventID")
                    or mention.get("Sub-eventID")
                    or "",
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "original_text": mention.get("original_text"),
                    "verbatim_ref": mention.get("verbatim_reference"),
                }
            )
    return rows


# --- per-category column maps (typed columns the site queries) ----------

PEOPLE_COLUMNS = {
    "person_id": "PersonID",
    "name": "name",
    "rank": "rank",
    "nationality": "nationality",
}
PLACE_COLUMNS = {
    "place_id": "PlaceID",
    "place_name": "current_name",
    "country": "country",
    "geocode_source": "geocode_source",
    "enrichment_status": "enrichment_status",
    "geo_review": "geo_review",
}
GROUP_COLUMNS = {
    "group_id": "GroupID",
    "group_name": "group_name",
    "group_type": "group_type",
    "nationality": "nationality",
}
EQUIPMENT_COLUMNS = {
    "equipment_id": "EquipmentID",
    "common_name": "common_name",
    "category": "category",
    "country_of_origin": "country_of_origin",
}
DATE_COLUMNS = {
    "date_id": "DateID",
    "date_start": "date_start",
    "date_end": "date_end",
    "date_precision": "date_precision",
    "normalized_datetime": "normalized_datetime",
    "original_text": "original_text",
}
LOGISTICS_COLUMNS = {
    "logistics_id": "LogisticsID",
    "logistics_type": "logistics_type",
    "category": "category",
    "description": "description",
    "severity": "severity",
    "status": "status",
}
def weather_rows(entity_dir: Path) -> List[Dict[str, Any]]:
    """Weather rows: flatten the nested ``location`` object (name + PlaceID).

    ``location`` is an object linking to a place; extract its ``place_name`` and
    ``PlaceID`` so weather can join to the places table.
    """
    rows: List[Dict[str, Any]] = []
    for data in _iter_entity_files(entity_dir):
        wid = data.get("WeatherID")
        if not wid:
            continue
        loc = data.get("location") or {}
        rows.append(
            {
                "weather_id": wid,
                "date_id": data.get("DateID"),
                "place_name": loc.get("place_name"),
                "place_id": loc.get("PlaceID"),
                "source_type": data.get("source_type"),
                "raw": _json(data),
            }
        )
    return rows


def casualty_rows(entity_dir: Path) -> List[Dict[str, Any]]:
    """Casualty rows: typed columns + flattened nested date; ``count`` as JSON.

    ``count`` is a nested object (per-type value/qualifier) preserved as a JSON
    string; the date lives under a nested ``date`` object.
    """
    rows: List[Dict[str, Any]] = []
    for data in _iter_entity_files(entity_dir):
        cid = data.get("CasualtyID")
        if not cid:
            continue
        date = data.get("date") or {}
        rows.append(
            {
                "casualty_id": cid,
                "type": data.get("type"),
                "description": data.get("description"),
                "count": _json(data.get("count")) if data.get("count") else None,
                "date_string": date.get("date_string"),
                "iso_date": date.get("iso_date"),
                "raw": _json(data),
            }
        )
    return rows


def casualty_mention_rows(entity_dir: Path) -> List[Dict[str, Any]]:
    """Casualties link via ``event_context``/``source`` (not event_mentions[]).

    Produce one mention row per casualty tying it to its sub-event, using the
    source's verbatim locus (book/chapter/paragraph) as the citation ref.
    """
    rows: List[Dict[str, Any]] = []
    for data in _iter_entity_files(entity_dir):
        cid = data.get("CasualtyID")
        if not cid:
            continue
        ctx = data.get("event_context") or {}
        src = data.get("source") or {}
        sub_event_id = ctx.get("Sub-eventID") or src.get("Sub-eventID") or ""
        locus_bits = [
            src.get("book"),
            src.get("chapter"),
            f"para {src.get('paragraph_number')}"
            if src.get("paragraph_number") is not None
            else None,
        ]
        verbatim = ", ".join(b for b in locus_bits if b) or None
        rows.append(
            {
                "mention_id": "",  # casualties carry no MentionID
                "sub_event_id": sub_event_id,
                "entity_type": "casualty",
                "entity_id": cid,
                "original_text": data.get("description"),
                "verbatim_ref": verbatim,
            }
        )
    return rows


def source_rows(entity_dir: Path) -> List[Dict[str, Any]]:
    """Bibliography rows, reading document_type/author from nested ``citation``.

    ``document_type`` (e.g. "Manuscript", "KTB") and ``author`` live under the
    ``citation`` object, not at the top level; ``availability`` and ``license``
    are top-level. Author is a list in the source and is preserved as a JSON
    string so the single ``author`` column keeps all names.
    """
    rows: List[Dict[str, Any]] = []
    for data in _iter_entity_files(entity_dir):
        bib_id = data.get("BibliographyID")
        if not bib_id:
            continue
        citation = data.get("citation") or {}
        authors = citation.get("author")
        rows.append(
            {
                "bibliography_id": bib_id,
                "title": data.get("title") or citation.get("title"),
                "author": _json(authors) if authors else None,
                "document_type": citation.get("document_type"),
                "availability": data.get("availability"),
                "archive_reference": data.get("archive_reference_number"),
                "license": data.get("license"),
                "raw": _json(data),
            }
        )
    return rows


def place_rows_with_coords(entity_dir: Path) -> List[Dict[str, Any]]:
    """Place rows including flattened nested ``coordinates`` lat/lon."""
    rows = to_entity_rows(entity_dir, "PlaceID", PLACE_COLUMNS)
    by_id = {r["place_id"]: r for r in rows}
    for data in _iter_entity_files(entity_dir):
        pid = data.get("PlaceID")
        if pid not in by_id:
            continue
        coords = data.get("coordinates") or {}
        by_id[pid]["latitude"] = coords.get("latitude")
        by_id[pid]["longitude"] = coords.get("longitude")
        by_id[pid]["geo_confidence"] = coords.get("confidence")
    return rows
