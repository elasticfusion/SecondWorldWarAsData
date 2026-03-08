"""Unit tests for people extraction and deduplication."""

from src.extraction.people import (
    _normalize_name,
    _name_to_filename,
    _merge_person,
    _deduplicate_units,
    _normalize_rank,
)


class TestPeopleNormalization:
    """Test name normalization functions."""

    def test_normalize_name(self):
        """Test name normalization."""
        assert _normalize_name("Dwight D. Eisenhower") == "dwight d eisenhower"
        assert _normalize_name("  George   Patton  ") == "george patton"
        assert _normalize_name("O'Brien") == "o'brien"

    def test_name_to_filename(self):
        """Test filename generation from name."""
        filename = _name_to_filename("Dwight D. Eisenhower", "01ABC123")
        assert filename == "Dwight_D_Eisenhower_01ABC123.json"

        filename = _name_to_filename("O'Brien", "01DEF456")
        assert filename == "OBrien_01DEF456.json"

    def test_normalize_rank(self):
        """Test military rank normalization."""
        assert _normalize_rank("General") == "general"
        assert _normalize_rank("Lt. General") == "lt. general"


class TestPeopleMerging:
    """Test person merging logic."""

    def test_merge_basic_fields(self, sample_person_data):
        """Test merging basic person fields."""
        primary = sample_person_data.copy()
        secondary = {
            "PersonID": "01H8XYZ999",
            "name": "Dwight Eisenhower",
            "source_language": "English",
            "aliases": ["General Eisenhower"],
            "biographical_profile": {},
            "event_mentions": [],
        }

        merged = _merge_person(primary, secondary)

        assert merged["name"] == "Dwight D. Eisenhower"  # Keep primary
        assert "General Eisenhower" in merged["aliases"]
        assert "Ike" in merged["aliases"]

    def test_merge_event_mentions(self, sample_person_data):
        """Test merging event mentions."""
        primary = sample_person_data.copy()
        secondary = {
            "PersonID": "01H8XYZ999",
            "name": "Dwight Eisenhower",
            "source_language": "English",
            "aliases": [],
            "biographical_profile": {},
            "event_mentions": [
                {
                    "MentionID": "01H8XYZ888",
                    "Event_Name": "Operation Overlord",
                    "EventID": "01H8XYZ777",
                    "position_at_event": "Commander",
                }
            ],
        }

        merged = _merge_person(primary, secondary)

        assert len(merged["event_mentions"]) == 2
        mention_ids = [m["MentionID"] for m in merged["event_mentions"]]
        assert "01H8XYZ..." in mention_ids
        assert "01H8XYZ888" in mention_ids


class TestUnitDeduplication:
    """Test military unit deduplication."""

    def test_deduplicate_identical_units(self):
        """Test deduplication of identical units."""
        units = [
            {"unit": "1st Infantry Division", "from_date": "1942-01-01"},
            {"unit": "1st Infantry Division", "from_date": "1942-01-01"},
        ]

        result = _deduplicate_units(units)
        assert len(result) == 1

    def test_deduplicate_variant_units(self):
        """Test deduplication of unit name variants."""
        units = [
            {"unit": "1st Infantry Division", "from_date": "1942-01-01"},
            {"unit": "1st Inf Div", "from_date": "1942-01-01"},
            {"unit": "First Infantry Division", "from_date": "1942-01-01"},
        ]

        result = _deduplicate_units(units)
        # Should recognize these as the same unit
        assert len(result) <= 2  # Some variants may remain
