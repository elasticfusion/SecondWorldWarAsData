"""Tests for batch_parallel.py extraction orchestration."""

# pylint: disable=missing-function-docstring

import json

import pytest


@pytest.fixture
def entity_dir(tmp_path):
    """Create a temporary entity directory with index."""
    d = tmp_path / "places"
    d.mkdir()
    (d / "index.json").write_text("{}", encoding="utf-8")
    return d


class TestGetOrCreateEntity:
    """Test _get_or_create_entity logic."""

    def test_creates_new_entity(self, entity_dir):
        from src.extraction.batch_parallel import _get_or_create_entity

        index = {}
        entity_file, entity_id, record = _get_or_create_entity(
            key="nancy",
            obj={"name": "Nancy", "latitude": 48.69, "longitude": 6.18},
            index=index,
            entity_dir=entity_dir,
            make_record=lambda o: {"current_name": o["name"]},
            id_field="PlaceID",
        )
        assert entity_file is not None
        assert entity_id is not None
        assert len(entity_id) == 26  # ULID
        assert record["current_name"] == "Nancy"
        assert record["PlaceID"] == entity_id
        assert "nancy" in index
        assert entity_file.exists()

    def test_returns_existing_entity(self, entity_dir):
        from src.extraction.batch_parallel import _get_or_create_entity

        existing = {"current_name": "Nancy", "PlaceID": "01TEST", "event_mentions": []}
        (entity_dir / "nancy.json").write_text(json.dumps(existing), encoding="utf-8")
        index = {"nancy": "nancy.json"}

        entity_file, _, record = _get_or_create_entity(
            key="nancy",
            obj={"name": "Nancy"},
            index=index,
            entity_dir=entity_dir,
            make_record=lambda o: {"current_name": o["name"]},
            id_field="PlaceID",
        )
        assert entity_file == entity_dir / "nancy.json"
        assert record["PlaceID"] == "01TEST"

    def test_handles_corrupted_file(self, entity_dir):
        from src.extraction.batch_parallel import _get_or_create_entity

        (entity_dir / "bad.json").write_text("not json", encoding="utf-8")
        index = {"bad": "bad.json"}

        _, _, record = _get_or_create_entity(
            key="bad",
            obj={"name": "Bad"},
            index=index,
            entity_dir=entity_dir,
            make_record=lambda o: {"current_name": o["name"]},
            id_field="PlaceID",
        )
        assert record == {}


class TestHelpers:
    """Test helper functions."""

    def test_strip_rank(self):
        from src.extraction.batch_parallel import _strip_rank

        assert _strip_rank("General Eisenhower") == "Eisenhower"
        assert _strip_rank("Col. Smith") == "Smith"
        assert _strip_rank("Eisenhower") == "Eisenhower"

    def test_is_not_a_person(self):
        from src.extraction.batch_parallel import _is_not_a_person

        assert _is_not_a_person("4th Infantry Division") is True
        assert _is_not_a_person("V Corps") is True
        assert _is_not_a_person("Eisenhower") is False

    def test_make_date_key_valid(self):
        from src.extraction.batch_parallel import _make_date_key

        obj = {"date_start": "1944-09-15"}
        key = _make_date_key(obj)
        assert key == "1944-09-15"

    def test_make_date_key_with_time(self):
        from src.extraction.batch_parallel import _make_date_key

        obj = {"date_start": "1944-09-15", "time_start": "06:30"}
        key = _make_date_key(obj)
        assert key == "1944-09-15T06:30"

    def test_make_date_key_missing(self):
        from src.extraction.batch_parallel import _make_date_key

        assert _make_date_key({}) == ""

    def test_make_date_key_rejects_only_garbage_years(self):
        from src.extraction.batch_parallel import _make_date_key

        # PATTERN 4: the WWII window is now ADVISORY — out-of-window but plausible years are KEPT
        # (publication/background dates), only clearly-garbage years are rejected.
        assert _make_date_key({"date_start": "2024-01-01"}) != ""  # kept (advisory)
        assert _make_date_key({"date_start": "1800-01-01"}) != ""  # kept (advisory)
        assert _make_date_key({"date_start": "0101-01-01"}) == ""  # garbage rejected
        assert _make_date_key({"date_start": "9999-01-01"}) == ""  # garbage rejected

    def test_load_index(self, tmp_path):
        from src.extraction.batch_parallel import _load_index

        idx_file = tmp_path / "index.json"
        idx_file.write_text('{"nancy": "nancy.json"}', encoding="utf-8")
        result = _load_index(idx_file, "places")
        assert result == {"nancy": "nancy.json"}

    def test_load_index_missing(self, tmp_path):
        from src.extraction.batch_parallel import _load_index

        result = _load_index(tmp_path / "nope.json", "places")
        assert result == {}

    def test_make_date_record(self):
        from src.extraction.batch_parallel import _make_date_record

        obj = {
            "date_start": "1944-09-15",
            "date_end": "1944-09-16",
            "date_precision": "exact",
        }
        record = _make_date_record(obj)
        assert record["date_start"] == "1944-09-15"
        assert record["date_end"] == "1944-09-16"

    def test_make_date_filename(self):
        from src.extraction.batch_parallel import _make_date_filename

        fn = _make_date_filename("1944-09-15", "01TESTULID12345678901234")
        assert fn.endswith(".json")
        assert "19440915" in fn


class TestProcessEntityObj:
    """Test _process_entity_obj."""

    def test_creates_entity_and_adds_mention(self, entity_dir):
        from src.extraction.batch_parallel import _process_entity_obj

        index = {}
        links = {"places": []}

        result = _process_entity_obj(
            obj={"name": "Nancy", "latitude": 48.69, "longitude": 6.18},
            make_key=lambda o: o.get("name", "").lower(),
            make_record=lambda o: {"current_name": o["name"]},
            entity_dir=entity_dir,
            index=index,
            id_field="PlaceID",
            seid="SE_001",
            se_name="Advance on Nancy",
            event_id="EV_001",
            event_name="Lorraine Campaign",
            meta={"book": "Test", "author": "Author", "series": "Series"},
            sub_event_key="places",
            links=links,
        )
        assert result is True
        assert "nancy" in index
        assert "SE_001" in links  # Links keyed by sub_event_id
        # Verify file was created with mention
        entity_file = entity_dir / index["nancy"]
        data = json.loads(entity_file.read_text(encoding="utf-8"))
        assert data["current_name"] == "Nancy"
        assert len(data["event_mentions"]) == 1
        assert data["event_mentions"][0]["Sub_eventID"] == "SE_001"

    def test_skips_empty_key(self, entity_dir):
        from src.extraction.batch_parallel import _process_entity_obj

        index = {}
        links = {"places": []}

        result = _process_entity_obj(
            obj={"name": ""},
            make_key=lambda o: o.get("name", "").lower().strip(),
            make_record=lambda o: {},
            entity_dir=entity_dir,
            index=index,
            id_field="PlaceID",
            seid="SE_001",
            se_name="Test",
            event_id="EV_001",
            event_name="Test",
            meta={"book": "Test", "author": "A", "series": "S"},
            sub_event_key="places",
            links=links,
        )
        assert result is False


def test_read_side_index_traversal_guard(tmp_path):
    """HIGH#3: a pre-fix/tampered index[key] containing '../' or an absolute path must NOT escape
    entity_dir on read, and the index entry self-heals to a safe basename."""
    from pathlib import Path
    from src.extraction.batch_parallel import (
        _get_or_create_entity,
        _safe_entity_filename,
    )

    entity_dir = tmp_path / "places"
    entity_dir.mkdir()
    # Plant a would-be target OUTSIDE entity_dir that a traversal could reach.
    outside = tmp_path / "secret.json"
    outside.write_text('{"stolen": true}', encoding="utf-8")

    # Pre-fix index maps a key to a traversal path.
    index = {"Aachen": "../secret.json"}
    entity_file, entity_id, record = _get_or_create_entity(
        key="Aachen",
        obj={},
        index=index,
        entity_dir=entity_dir,
        make_record=lambda o: {},
        id_field="PlaceID",
    )
    # Resolved path must stay inside entity_dir (no escape to ../secret.json).
    assert Path(entity_file).resolve().parent == entity_dir.resolve()
    assert ".." not in str(entity_file)
    # The out-of-dir secret was never read as the entity record.
    assert record.get("stolen") is not True
    # Index self-healed to a safe basename.
    assert index["Aachen"] == _safe_entity_filename("../secret.json", "Aachen")
    assert "/" not in index["Aachen"]


def test_relationship_index_traversal_guard(tmp_path):
    """HIGH#3 (security follow-up): _process_place_relationships must not join a tampered index
    value verbatim — that would read+write a file OUTSIDE entity_dir. Sanitize to a safe basename.
    """
    import json as _json
    from src.extraction.batch_parallel import _process_place_relationships

    entity_dir = tmp_path / "places"
    entity_dir.mkdir()
    # An out-of-dir file a traversal relationship would try to read AND overwrite.
    outside = tmp_path / "victim.json"
    outside.write_text('{"PlaceID": "VICTIM", "untouched": true}', encoding="utf-8")
    # Legit in-dir place the relationship points from.
    (entity_dir / "aachen.json").write_text(
        _json.dumps({"PlaceID": "01A", "related_places": []}), encoding="utf-8"
    )

    # Pre-fix/tampered index: 'koln' maps to a traversal path to the out-of-dir victim.
    index = {"aachen": "aachen.json", "koln": "../victim.json"}
    response = {
        "places": [
            {"relationships": [{"type": "near", "from": "aachen", "to": "koln"}]}
        ]
    }
    _process_place_relationships(response, entity_dir, index)

    # The out-of-dir victim must be neither read-as-target nor overwritten.
    assert _json.loads(outside.read_text())["untouched"] is True
    # Index self-healed to a safe basename (no traversal propagated).
    assert index["koln"] == "victim.json" and "/" not in index["koln"]


def test_contained_entity_path_blocks_symlink_escape(tmp_path):
    """HIGH#3 #450 follow-up: a symlink at entity_dir/<name>.json pointing OUTSIDE the tree must be
    refused (fail-closed), even though the name itself is lexically safe."""
    from src.extraction.batch_parallel import _contained_entity_path

    ed = tmp_path / "places"
    ed.mkdir()
    outside = tmp_path / "secret.json"
    outside.write_text("{}")
    (ed / "evil.json").symlink_to(outside)  # lexically-safe name, symlink escapes
    assert _contained_entity_path(ed, "evil.json") is None
    # A normal (non-symlink) in-dir file is allowed.
    (ed / "berlin.json").write_text("{}")
    assert _contained_entity_path(ed, "berlin.json") is not None
    # An in-dir symlink (stays inside) is allowed.
    (ed / "real.json").write_text("{}")
    (ed / "alias.json").symlink_to(ed / "real.json")
    assert _contained_entity_path(ed, "alias.json") is not None


def test_get_or_create_entity_read_refuses_symlink_escape(tmp_path):
    """The read path must not follow a symlinked index entry out of entity_dir."""
    import json as _json
    from src.extraction.batch_parallel import _get_or_create_entity

    ed = tmp_path / "places"
    ed.mkdir()
    secret = tmp_path / "secret.json"
    secret.write_text(_json.dumps({"stolen": True, "PlaceID": "X"}))
    (ed / "aachen.json").symlink_to(secret)  # index points to a symlink escaping ed
    index = {"Aachen": "aachen.json"}
    entity_file, eid, record = _get_or_create_entity(
        key="Aachen",
        obj={},
        index=index,
        entity_dir=ed,
        make_record=lambda o: {},
        id_field="PlaceID",
    )
    assert record.get("stolen") is not True  # out-of-dir secret NOT read


def test_process_entity_obj_blocks_symlink_write_through(tmp_path):
    """#450 CRITICAL regression: a symlinked index entry must NOT be read OR written-through by the
    follow-on event-mention write. The out-of-dir target stays untouched."""
    import json as _json
    from src.extraction import batch_parallel as bp

    ed = tmp_path / "places"
    ed.mkdir()
    secret = tmp_path / "secret.json"
    secret.write_text(_json.dumps({"PlaceID": "X", "stolen": True}))
    (ed / "aachen.json").symlink_to(secret)
    index = {"Aachen": "aachen.json"}

    processed = bp._process_entity_obj(
        obj={"name": "Aachen"},
        make_key=lambda o: "Aachen",
        make_record=lambda o: {},
        entity_dir=ed,
        index=index,
        id_field="PlaceID",
        seid="01HX7YZABCDEFGHJKMNPQRSTVW",
        se_name="s",
        event_id="01EV7YZABCDEFGHJKMNPQRSTVW",
        event_name="e",
        meta={"book": "b"},
        sub_event_key="places",
        links={},
    )
    assert processed is False  # containment blocked -> skipped
    after = _json.loads(secret.read_text())
    assert after.get("stolen") is True
    assert "event_mentions" not in after  # NOT written-through the symlink
