"""Tests for src/dedup/merge.py — entity merge and event ref updates."""

# pylint: disable=missing-function-docstring

import json

import pytest


@pytest.fixture
def output_root(tmp_path):
    """Create a minimal output directory with people and event files."""
    people_dir = tmp_path / "people"
    people_dir.mkdir()
    (people_dir / "index.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture
def two_people(output_root):
    """Create two people files that are duplicates."""
    people_dir = output_root / "people"
    primary = {
        "PersonID": "01PRIMARY000000000000AB",
        "name": "Omar Bradley",
        "event_mentions": [
            {"MentionID": "M1", "Sub_eventID": "SE_001", "Event_Name": "Lorraine"}
        ],
    }
    secondary = {
        "PersonID": "01SECOND0000000000000AB",
        "name": "Omar N. Bradley",
        "event_mentions": [
            {"MentionID": "M2", "Sub_eventID": "SE_002", "Event_Name": "Bulge"}
        ],
    }
    (people_dir / "omar bradley.json").write_text(json.dumps(primary), encoding="utf-8")
    (people_dir / "omar n bradley.json").write_text(
        json.dumps(secondary), encoding="utf-8"
    )
    return [
        {"filename": "omar bradley.json", "name": "Omar Bradley"},
        {"filename": "omar n bradley.json", "name": "Omar N. Bradley"},
    ]


class TestUpdateEventRefs:
    def test_replaces_old_id_in_event_files(self, output_root):
        from src.dedup.merge import update_event_refs

        content_dir = output_root / "content" / "book1"
        content_dir.mkdir(parents=True)
        event = {
            "Event": {
                "EventID": "EV1",
                "Sub-events": [{"Sub-eventID": "SE1", "people": ["OLD_ID", "OTHER"]}],
            }
        }
        (content_dir / "ch01-event.json").write_text(
            json.dumps(event), encoding="utf-8"
        )

        update_event_refs(output_root, "OLD_ID", "NEW_ID", "people")

        result = json.loads(
            (content_dir / "ch01-event.json").read_text(encoding="utf-8")
        )
        assert result["Event"]["Sub-events"][0]["people"] == ["NEW_ID", "OTHER"]

    def test_replaces_in_entity_subdirs(self, output_root):
        from src.dedup.merge import update_event_refs

        logistics_dir = output_root / "logistics"
        logistics_dir.mkdir()
        data = {"PersonID": "OLD_ID", "note": "test"}
        (logistics_dir / "supply.json").write_text(json.dumps(data), encoding="utf-8")

        update_event_refs(output_root, "OLD_ID", "NEW_ID", "people")

        result = json.loads((logistics_dir / "supply.json").read_text(encoding="utf-8"))
        assert "NEW_ID" in json.dumps(result)
        assert "OLD_ID" not in json.dumps(result)

    def test_handles_corrupted_event_file(self, output_root):
        from src.dedup.merge import update_event_refs

        content_dir = output_root / "content" / "book1"
        content_dir.mkdir(parents=True)
        (content_dir / "bad-event.json").write_text("not json", encoding="utf-8")

        # Should not raise
        update_event_refs(output_root, "OLD", "NEW", "people")


class TestUpdateIndex:
    def test_updates_index_entry(self, output_root):
        from src.dedup.merge import update_index

        index_path = output_root / "people" / "index.json"
        index_path.write_text(
            json.dumps({"omar n. bradley": "omar n bradley.json"}), encoding="utf-8"
        )

        update_index(index_path, "Omar N. Bradley", "omar bradley.json")

        result = json.loads(index_path.read_text(encoding="utf-8"))
        assert result["omar n. bradley"] == "omar bradley.json"

    def test_noop_if_index_missing(self, tmp_path):
        from src.dedup.merge import update_index

        # Should not raise
        update_index(tmp_path / "nope.json", "test", "test.json")


class TestMergeGeneric:
    def test_merges_event_mentions_and_aliases(self, output_root):
        from src.dedup.merge import merge_generic

        equip_dir = output_root / "equipment"
        equip_dir.mkdir()
        primary = {
            "EquipmentID": "01EQ1",
            "common_name": "Sherman",
            "event_mentions": [{"Sub_eventID": "SE1", "MentionID": "M1"}],
            "aliases": [],
        }
        secondary = {
            "EquipmentID": "01EQ2",
            "common_name": "M4 Sherman",
            "event_mentions": [{"Sub_eventID": "SE2", "MentionID": "M2"}],
        }
        (equip_dir / "sherman.json").write_text(json.dumps(primary), encoding="utf-8")
        (equip_dir / "m4 sherman.json").write_text(
            json.dumps(secondary), encoding="utf-8"
        )

        people = [
            {"filename": "sherman.json", "name": "Sherman"},
            {"filename": "m4 sherman.json", "name": "M4 Sherman"},
        ]
        result = merge_generic(equip_dir, people, primary_idx=0, id_field="EquipmentID")

        assert result == "Sherman"
        assert not (equip_dir / "m4 sherman.json").exists()

        merged = json.loads((equip_dir / "sherman.json").read_text(encoding="utf-8"))
        assert len(merged["event_mentions"]) == 2
        assert "M4 Sherman" in merged["aliases"]

    def test_deduplicates_mentions_by_sub_event_id(self, output_root):
        from src.dedup.merge import merge_generic

        equip_dir = output_root / "equipment"
        equip_dir.mkdir()
        primary = {
            "EquipmentID": "01EQ1",
            "common_name": "Tiger",
            "event_mentions": [{"Sub_eventID": "SE1", "MentionID": "M1"}],
            "aliases": [],
        }
        secondary = {
            "EquipmentID": "01EQ2",
            "common_name": "Tiger I",
            "event_mentions": [{"Sub_eventID": "SE1", "MentionID": "M1_dup"}],
        }
        (equip_dir / "tiger.json").write_text(json.dumps(primary), encoding="utf-8")
        (equip_dir / "tiger i.json").write_text(json.dumps(secondary), encoding="utf-8")

        people = [
            {"filename": "tiger.json", "name": "Tiger"},
            {"filename": "tiger i.json", "name": "Tiger I"},
        ]
        merge_generic(equip_dir, people, primary_idx=0)

        merged = json.loads((equip_dir / "tiger.json").read_text(encoding="utf-8"))
        assert len(merged["event_mentions"]) == 1  # Deduped

    def test_returns_none_on_missing_primary(self, tmp_path):
        from src.dedup.merge import merge_generic

        people = [{"filename": "nope.json", "name": "Nope"}]
        assert merge_generic(tmp_path, people, primary_idx=0) is None


class TestDoMerge:
    def test_merges_and_deletes_secondary(self, output_root, two_people):
        from src.dedup.merge import do_merge

        people_dir = output_root / "people"
        result = do_merge(people_dir, two_people, primary_idx=0)

        assert result == "Omar Bradley"
        assert not (people_dir / "omar n bradley.json").exists()

        merged = json.loads(
            (people_dir / "omar bradley.json").read_text(encoding="utf-8")
        )
        assert merged["PersonID"] == "01PRIMARY000000000000AB"
        assert len(merged["event_mentions"]) >= 2

    def test_returns_none_on_missing_primary(self, output_root):
        from src.dedup.merge import do_merge

        people_dir = output_root / "people"
        people = [{"filename": "nope.json", "name": "Nope"}]
        assert do_merge(people_dir, people, primary_idx=0) is None
