"""Load transformed ``output/`` rows into the relational schema.

Thin DB-API adapter over :mod:`src.loader.transform`. Works with any PEP-249
connection: sqlite3 (tests, local exploration) now, and psycopg/psycopg2 against
Aurora PostgreSQL later without changing the transform layer. Postgres-only
concerns (pgvector embeddings, PostGIS geometry, HNSW indexes) are applied
separately from ``schema_pg_extras.sql`` and the embedding pass; this module
loads the portable core.

Order matters (referential): sources → events → sub_events → entities →
mentions. Loads are idempotent via INSERT-OR-REPLACE semantics on primary keys.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from src.loader import transform as T

logger = logging.getLogger(__name__)

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def create_schema(conn: Any) -> None:
    """Create the core tables/indexes from schema.sql (idempotent)."""
    conn.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()


def _insert(conn: Any, table: str, rows: List[Dict[str, Any]]) -> int:
    """Insert rows (dict per row) into ``table``; returns count inserted.

    Uses ``INSERT OR REPLACE`` (sqlite) — the loader targets sqlite for tests;
    the Postgres adapter overrides this with ``ON CONFLICT DO UPDATE``.
    """
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    collist = ", ".join(columns)
    sql = f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})"
    conn.executemany(sql, [[r.get(c) for c in columns] for r in rows])
    conn.commit()
    return len(rows)


def load_all(conn: Any, output_root: Path) -> Dict[str, int]:
    """Load the full ``output/`` tree into ``conn``; return per-table counts.

    Populates the hub first (events/sub-events), then entities, then the
    mention junction spanning all entity types.
    """
    create_schema(conn)
    counts: Dict[str, int] = {}

    hub = T.to_event_rows(output_root / "content")
    counts["events"] = _insert(conn, "events", hub["events"])
    counts["sub_events"] = _insert(conn, "sub_events", hub["sub_events"])

    counts["sources"] = _insert(
        conn,
        "sources",
        T.source_rows(output_root / "bibliography"),
    )
    counts["people"] = _insert(
        conn,
        "people",
        T.to_entity_rows(output_root / "people", "PersonID", T.PEOPLE_COLUMNS),
    )
    counts["people_groups"] = _insert(
        conn,
        "people_groups",
        T.to_entity_rows(output_root / "people_groups", "GroupID", T.GROUP_COLUMNS),
    )
    counts["places"] = _insert(
        conn, "places", T.place_rows_with_coords(output_root / "places")
    )
    counts["equipment"] = _insert(
        conn,
        "equipment",
        T.to_entity_rows(output_root / "equipment", "EquipmentID", T.EQUIPMENT_COLUMNS),
    )

    counts["mentions"] = _load_mentions(conn, output_root)
    logger.info("Load complete: %s", counts)
    return counts


_MENTION_SOURCES = [
    ("people", "person", "PersonID"),
    ("people_groups", "group", "GroupID"),
    ("places", "place", "PlaceID"),
    ("equipment", "equipment", "EquipmentID"),
    ("dates", "date", "DateID"),
    ("casualties", "casualty", "CasualtyID"),
    ("logistics", "logistics", "LogisticsID"),
    ("weather", "weather", "WeatherID"),
]


def _load_mentions(conn: Any, output_root: Path) -> int:
    """Flatten every entity type's event_mentions into the mentions table."""
    total = 0
    for subdir, entity_type, id_field in _MENTION_SOURCES:
        rows = T.to_mention_rows(output_root / subdir, entity_type, id_field)
        total += _insert(conn, "mentions", rows)
    return total
