"""Golden file tests — verify extraction output structure against known-good reference.

These tests validate that:
1. Phase 1 parsing produces expected structure
2. Entity extraction produces expected field presence and types
3. Schema changes don't silently break output format

Uses chapter99 (test chapter) as the reference input.
"""

# pylint: disable=missing-function-docstring

import json
from pathlib import Path

import pytest

CONTENT_DIR = Path("output/content/TheLorraineCampaign")
OUTPUT_DIR = Path("output")


@pytest.fixture
def parsed_chapter():
    """Load chapter99 parsed file."""
    f = CONTENT_DIR / "chapter99a-parsed.json"
    if not f.exists():
        pytest.skip("chapter99 parsed file not available")
    return json.loads(f.read_text(encoding="utf-8"))


@pytest.fixture
def event_file():
    """Load chapter99 event file."""
    f = CONTENT_DIR / "chapter99a-event.json"
    if not f.exists():
        pytest.skip("chapter99 event file not available")
    return json.loads(f.read_text(encoding="utf-8"))


class TestPhase1ParsedOutput:
    """Verify Phase 1 parsed output matches expected structure."""

    def test_has_required_fields(self, parsed_chapter):
        required = ["book", "chapter_number", "chapter_title", "paragraphs"]
        for field in required:
            assert field in parsed_chapter, f"Missing field: {field}"

    def test_book_metadata(self, parsed_chapter):
        assert parsed_chapter["book"] == "The Lorraine Campaign"
        assert parsed_chapter["chapter_number"] == 99

    def test_paragraphs_structure(self, parsed_chapter):
        paras = parsed_chapter["paragraphs"]
        assert len(paras) >= 1
        for p in paras:
            assert "text" in p
            assert "absolute_number" in p

    def test_has_source_hash(self, parsed_chapter):
        assert "_source_hash" in parsed_chapter


class TestPhase2EventOutput:
    """Verify Phase 2 event extraction structure."""

    def test_has_event_structure(self, event_file):
        assert "Event" in event_file
        event = event_file["Event"]
        assert "EventID" in event
        assert "Sub-events" in event
        assert len(event["EventID"]) == 26  # ULID

    def test_sub_event_structure(self, event_file):
        sub_events = event_file["Event"]["Sub-events"]
        assert len(sub_events) >= 1
        se = sub_events[0]
        assert "Sub-eventID" in se
        assert "Sub-event_summary" in se
        assert len(se["Sub-eventID"]) == 26

    def test_sub_event_has_fulltext(self, event_file):
        se = event_file["Event"]["Sub-events"][0]
        assert "Sub-event_fulltext" in se
        fulltext = se["Sub-event_fulltext"]
        assert isinstance(fulltext, dict)
        # Fulltext should contain the source paragraph text
        all_text = " ".join(str(v) for v in fulltext.values())
        assert "35th Infantry Division" in all_text or "Grémecey" in all_text

    def test_chapter_metadata(self, event_file):
        assert "Chapter" in event_file


class TestEntityFileStructure:
    """Verify entity files have correct structure."""

    def test_people_file_structure(self):
        people_dir = OUTPUT_DIR / "people"
        if not people_dir.exists():
            pytest.skip("people directory not available")
        files = [f for f in people_dir.glob("*.json") if f.name != "index.json"]
        if not files:
            pytest.skip("no people files")
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "PersonID" in data
        assert "name" in data
        assert len(data["PersonID"]) == 26

    def test_places_file_structure(self):
        places_dir = OUTPUT_DIR / "places"
        if not places_dir.exists():
            pytest.skip("places directory not available")
        files = [
            f
            for f in places_dir.glob("*.json")
            if f.name
            not in (
                "index.json",
                "coords.json",
                "duplicate_report.json",
                "not_duplicates.json",
            )
        ]
        if not files:
            pytest.skip("no place files")
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "PlaceID" in data
        assert len(data["PlaceID"]) == 26
        # Should have coordinates
        has_coords = data.get("latitude") is not None or (
            isinstance(data.get("coordinates"), dict)
            and data["coordinates"].get("latitude")
        )
        assert has_coords, f"Place {files[0].name} missing coordinates"

    def test_dates_file_structure(self):
        dates_dir = OUTPUT_DIR / "dates"
        if not dates_dir.exists():
            pytest.skip("dates directory not available")
        files = [f for f in dates_dir.glob("*.json") if f.name != "index.json"]
        if not files:
            pytest.skip("no date files")
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "DateID" in data
        assert "date_start" in data
        assert len(data["DateID"]) == 26

    def test_equipment_file_structure(self):
        equip_dir = OUTPUT_DIR / "equipment"
        if not equip_dir.exists():
            pytest.skip("equipment directory not available")
        skip = {"index.json", "duplicate_report.json", "not_duplicates.json"}
        files = [f for f in equip_dir.glob("*.json") if f.name not in skip]
        if not files:
            pytest.skip("no equipment files")
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "EquipmentID" in data
        assert "common_name" in data
        assert len(data["EquipmentID"]) == 26

    def test_index_file_format(self):
        """All entity index.json files should map name→filename."""
        for entity_type in ["people", "places", "people_groups", "equipment", "dates"]:
            idx_file = OUTPUT_DIR / entity_type / "index.json"
            if not idx_file.exists():
                continue
            data = json.loads(idx_file.read_text(encoding="utf-8"))
            assert isinstance(data, dict)
            for name, filename in list(data.items())[:3]:
                assert isinstance(name, str)
                assert isinstance(filename, str)
                assert filename.endswith(".json")
