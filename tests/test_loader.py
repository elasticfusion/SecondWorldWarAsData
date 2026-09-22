"""Tests for the output/ -> relational schema loader (sqlite, no network/DB server)."""

import json
import sqlite3
from pathlib import Path

from src.loader import transform as T
from src.loader.load import load_all


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _fixture_output(root: Path) -> Path:
    out = root / "output"
    # event hub
    _write(
        out / "content" / "TestBook" / "chapter1a-event.json",
        {
            "Chapter": "The Test",
            "Event": {
                "EventID": "EV1",
                "Event_Name": "Test Event",
                "Sub-events": [
                    {
                        "Sub-eventID": "SE1",
                        "Sub-event_summary": "A summary.",
                        "Sub-event_fulltext": {
                            "Paragraph_1": "Para one.",
                            "Paragraph_2": "Para two.",
                        },
                        "Endnote_References": [3],
                        "Footnote_References": [],
                    }
                ],
            },
        },
    )
    # person linked to the sub-event
    _write(
        out / "people" / "collins.json",
        {
            "PersonID": "P1",
            "name": "J. Lawton Collins",
            "rank": "Maj Gen",
            "nationality": "USA",
            "event_mentions": [
                {
                    "MentionID": "M1",
                    "Sub_eventID": "SE1",
                    "original_text": "Collins ordered...",
                    "verbatim_reference": "p.10",
                }
            ],
        },
    )
    # place with nested coordinates + geocode provenance
    _write(
        out / "places" / "aachen.json",
        {
            "PlaceID": "PL1",
            "current_name": "Aachen",
            "country": "Germany",
            "geocode_source": "osm",
            "enrichment_status": "geocoded",
            "coordinates": {"latitude": 50.77, "longitude": 6.08, "confidence": 0.9},
            "event_mentions": [{"Sub_eventID": "SE1"}],  # no MentionID (null id)
        },
    )
    _write(
        out / "bibliography" / "src1.json",
        {
            "BibliographyID": "B1",
            "title": "A Report",
            "availability": "archive",
            "license": "public_domain",
            "archive_reference_number": "RG 319",
            "citation": {
                "document_type": "Manuscript",
                "author": ["Heichler, Lucian"],
            },
        },
    )
    # casualty: links via event_context/source (not event_mentions[])
    _write(
        out / "casualties" / "killed1.json",
        {
            "CasualtyID": "C1",
            "type": "killed",
            "description": "German dead after the pocket battle",
            "count": {"killed": {"value": 10000, "qualifier": "exact"}},
            "event_context": {"EventID": "EV1", "Sub-eventID": "SE1"},
            "source": {
                "book": "Breakout And Pursuit",
                "chapter": "Closing the Pocket",
                "paragraph_number": 177,
            },
            "date": {"date_string": "1944-08", "iso_date": "1944-08"},
        },
    )
    # weather: nested location object linking to a place
    _write(
        out / "weather" / "w1.json",
        {
            "WeatherID": "W1",
            "DateID": "D1",
            "location": {"place_name": "near Metz", "PlaceID": "PL1"},
            "source_type": "extracted",
        },
    )
    return out


def test_load_all_populates_hub_and_entities(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    counts = load_all(conn, out)
    assert counts["events"] == 1
    assert counts["sub_events"] == 1
    assert counts["people"] == 1
    assert counts["places"] == 1
    assert counts["sources"] == 1
    assert counts["casualties"] == 1
    assert counts["weather"] == 1
    # person mention + place mention + casualty mention (via event_context)
    assert counts["mentions"] == 3


def test_casualty_links_to_subevent_with_synthesized_citation(tmp_path: Path) -> None:
    """Casualties carry source (book/chapter/para) though no event_mentions[]."""
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    row = conn.execute(
        "SELECT sub_event_id, verbatim_ref FROM mentions "
        "WHERE entity_type='casualty' AND entity_id='C1'"
    ).fetchone()
    assert row is not None, "casualty must produce a mention row"
    sub_event_id, verbatim_ref = row
    assert sub_event_id == "SE1"  # resolved from event_context
    # citation synthesized from the structured source locus
    assert "Breakout And Pursuit" in verbatim_ref
    assert "para 177" in verbatim_ref


def test_no_dangling_mentions(tmp_path: Path) -> None:
    """Every mention resolves to a loaded entity (no dangling entity_id)."""
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    checks = [
        ("person", "people", "person_id"),
        ("place", "places", "place_id"),
        ("casualty", "casualties", "casualty_id"),
    ]
    for entity_type, table, id_col in checks:
        dangling = conn.execute(
            f"SELECT COUNT(*) FROM mentions m WHERE m.entity_type=? "
            f"AND NOT EXISTS (SELECT 1 FROM {table} e WHERE e.{id_col}=m.entity_id)",
            (entity_type,),
        ).fetchone()[0]
        assert dangling == 0, f"{entity_type} has {dangling} dangling mentions"


def test_weather_location_flattened_to_place(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    row = conn.execute(
        "SELECT place_name, place_id FROM weather WHERE weather_id='W1'"
    ).fetchone()
    assert row == ("near Metz", "PL1")  # nested location.* flattened + linkable


def test_subevent_fulltext_flattened(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    ft = conn.execute(
        "SELECT fulltext FROM sub_events WHERE sub_event_id='SE1'"
    ).fetchone()[0]
    assert "Para one." in ft and "Para two." in ft


def test_place_coordinates_flattened(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    row = conn.execute(
        "SELECT latitude, longitude, geocode_source, geo_confidence FROM places WHERE place_id='PL1'"
    ).fetchone()
    assert row == (50.77, 6.08, "osm", 0.9)


def test_mentions_join_resolves(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    # person -> sub_event -> event join
    row = conn.execute("""SELECT p.name, e.event_name
           FROM mentions m
           JOIN people p ON m.entity_id = p.person_id AND m.entity_type='person'
           JOIN sub_events se ON m.sub_event_id = se.sub_event_id
           JOIN events e ON se.event_id = e.event_id""").fetchone()
    assert row == ("J. Lawton Collins", "Test Event")


def test_load_is_idempotent(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    load_all(conn, out)  # second load must not duplicate PK rows
    assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    # mentions must be idempotent too (composite PK); fixture has 3 mentions
    # (person + place via event_mentions[], casualty via event_context)
    assert conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0] == 3
    # rows without a MentionID (place mention + casualty) coalesce to "" so
    # they still de-duplicate on reload rather than piling up
    null_id = conn.execute(
        "SELECT COUNT(*) FROM mentions WHERE mention_id = ''"
    ).fetchone()[0]
    assert null_id == 2


def test_raw_blob_preserved(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    raw = conn.execute("SELECT raw FROM people WHERE person_id='P1'").fetchone()[0]
    assert json.loads(raw)["rank"] == "Maj Gen"  # full fidelity kept


def test_transform_event_rows_direct(tmp_path: Path) -> None:
    out = _fixture_output(tmp_path)
    hub = T.to_event_rows(out / "content")
    assert hub["events"][0]["event_id"] == "EV1"
    assert hub["sub_events"][0]["event_id"] == "EV1"


def test_non_sqlite_connection_rejected(tmp_path: Path) -> None:
    """Loader emits SQLite dialect only -> non-sqlite conn fails fast & clearly."""
    import pytest

    class FakePostgresConn:  # duck-types a DB-API conn but isn't sqlite3
        def executescript(self, *_a, **_k):  # would exist? psycopg doesn't
            raise AssertionError("guard should trip before this is called")

    out = _fixture_output(tmp_path)
    with pytest.raises(NotImplementedError, match="SQLite connections only"):
        load_all(FakePostgresConn(), out)


def test_source_document_type_from_nested_citation(tmp_path: Path) -> None:
    """document_type/author come from citation.*, not top-level availability."""
    out = _fixture_output(tmp_path)
    conn = sqlite3.connect(":memory:")
    load_all(conn, out)
    row = conn.execute(
        "SELECT document_type, availability, author, archive_reference "
        "FROM sources WHERE bibliography_id='B1'"
    ).fetchone()
    document_type, availability, author, archive_reference = row
    assert document_type == "Manuscript"  # from citation.document_type
    assert availability == "archive"  # top-level, no longer overwriting doc type
    assert json.loads(author) == ["Heichler, Lucian"]  # from citation.author[]
    assert archive_reference == "RG 319"
