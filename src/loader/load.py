"""Load transformed ``output/`` rows into the relational schema.

Thin loader over :mod:`src.loader.transform`. **Runs against SQLite today**
(tests and local exploration). The transform layer is fully DB-agnostic, so a
Postgres path (psycopg against Aurora) is a contained future addition — but it
does not exist yet: the SQL emitted here is SQLite dialect (``executescript``,
``INSERT OR REPLACE``, ``?`` placeholders), so a non-sqlite connection is
rejected (see ``_require_sqlite``). Adding Postgres means a small dialect seam
(``ON CONFLICT DO UPDATE``, ``%s`` params, per-statement DDL execution) plus the
still-to-be-written ``schema_pg_extras.sql`` for pgvector/PostGIS/HNSW; the
transform layer stays unchanged. Tracked in ``docs/current/TODO.md``.

Order matters (referential): sources → events → sub_events → entities →
mentions. Loads are idempotent via INSERT-OR-REPLACE semantics on primary keys.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from src.loader import transform as T

logger = logging.getLogger(__name__)

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def _require_sqlite(conn: Any) -> None:
    """Guard: this loader emits SQLite-dialect SQL only.

    Fails fast with a clear message if handed a non-sqlite connection, rather
    than dying obscurely inside ``executescript``/``INSERT OR REPLACE`` (which
    psycopg neither implements nor parses). Remove once the Postgres dialect
    adapter lands (see module docstring / TODO.md).
    """
    if not isinstance(conn, sqlite3.Connection):
        raise NotImplementedError(
            "src.loader currently supports SQLite connections only; the Postgres "
            "dialect adapter (ON CONFLICT / %s params / schema_pg_extras.sql) is "
            f"not implemented yet. Got connection type {type(conn).__name__!r}. "
            "See docs/current/TODO.md."
        )


def create_schema(conn: Any) -> None:
    """Create the core tables/indexes from schema.sql (idempotent, SQLite)."""
    _require_sqlite(conn)
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
    T.reset_skips()
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
    counts["dates"] = _insert(
        conn,
        "dates",
        T.to_entity_rows(output_root / "dates", "DateID", T.DATE_COLUMNS),
    )
    counts["casualties"] = _insert(
        conn, "casualties", T.casualty_rows(output_root / "casualties")
    )
    counts["logistics"] = _insert(
        conn,
        "logistics",
        T.to_entity_rows(output_root / "logistics", "LogisticsID", T.LOGISTICS_COLUMNS),
    )
    counts["weather"] = _insert(
        conn,
        "weather",
        T.weather_rows(output_root / "weather"),
    )
    counts["images"] = _insert(conn, "images", T.image_rows(output_root / "images"))
    counts["maps"] = _insert(conn, "maps", T.map_rows(output_root / "maps"))

    counts["mentions"] = _load_mentions(conn, output_root)

    counts["skipped"] = T.skip_count()
    if counts["skipped"]:
        logger.warning(
            "Load complete with %d skipped file(s): %s | counts=%s",
            counts["skipped"],
            T.skip_breakdown(),
            {k: v for k, v in counts.items() if k != "skipped"},
        )
    else:
        logger.info("Load complete (0 skipped): %s", counts)
    return counts


_MENTION_SOURCES = [
    ("people", "person", "PersonID"),
    ("people_groups", "group", "GroupID"),
    ("places", "place", "PlaceID"),
    ("equipment", "equipment", "EquipmentID"),
    ("dates", "date", "DateID"),
    ("logistics", "logistics", "LogisticsID"),
    ("weather", "weather", "WeatherID"),
]
# casualties link via event_context/source, not event_mentions[] — handled
# separately in _load_mentions via T.casualty_mention_rows.


def _load_mentions(conn: Any, output_root: Path) -> int:
    """Flatten every entity type's event_mentions into the mentions table."""
    total = 0
    for subdir, entity_type, id_field in _MENTION_SOURCES:
        rows = T.to_mention_rows(output_root / subdir, entity_type, id_field)
        total += _insert(conn, "mentions", rows)
    total += _insert(
        conn, "mentions", T.casualty_mention_rows(output_root / "casualties")
    )
    # images and maps carry EventID/Sub-eventID inline (no event_mentions[])
    total += _insert(conn, "mentions", T.image_mention_rows(output_root / "images"))
    total += _insert(conn, "mentions", T.map_mention_rows(output_root / "maps"))
    return total
