"""Unit tests for people extraction and deduplication."""

from src.extraction.people import (
    _normalize_name,
    _name_to_filename,
    _normalize_rank,
)


class TestPeopleNormalization:
    """Test name normalization functions."""

    def test_normalize_name(self):
        """Test name normalization."""
        # _normalize_name only lowercases and strips, doesn't remove periods
        assert _normalize_name("Dwight D. Eisenhower") == "dwight d eisenhower"
        assert _normalize_name("  George   Patton  ") == "george patton"
        assert _normalize_name("O'Brien") == "obrien"

    def test_name_to_filename(self):
        """Test filename generation from name."""
        filename = _name_to_filename("Dwight D. Eisenhower", "01ABC123")
        assert filename == "Dwight_D_Eisenhower_01ABC123.json"

        filename = _name_to_filename("O'Brien", "01DEF456")
        assert filename == "OBrien_01DEF456.json"

    def test_normalize_rank(self):
        """Test military rank normalization."""
        # _normalize_rank expands abbreviations, doesn't lowercase
        assert _normalize_rank("Gen.") == "General"
        assert _normalize_rank("Lt. Gen.") == "Lieutenant General"
        assert _normalize_rank("General") == "General"  # No change if not abbreviated
