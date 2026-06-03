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

    def test_make_date_key_rejects_non_wwii(self):
        from src.extraction.batch_parallel import _make_date_key

        assert _make_date_key({"date_start": "2024-01-01"}) == ""
        assert _make_date_key({"date_start": "1800-01-01"}) == ""

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
