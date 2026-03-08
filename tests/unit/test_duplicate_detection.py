"""Unit tests for duplicate detection logic."""

import pytest
from scripts.find_duplicate_people import (
    _normalize_unicode,
    _normalize_german,
    _similarity_ratio,
    _extract_last_name,
)


class TestNameNormalization:
    """Test name normalization functions."""

    def test_normalize_unicode(self):
        """Test Unicode to ASCII normalization."""
        assert _normalize_unicode("Dönitz") == "Donitz"
        assert _normalize_unicode("Müller") == "Muller"
        assert _normalize_unicode("Eisenhower") == "Eisenhower"

    def test_normalize_german(self):
        """Test German umlaut normalization."""
        assert _normalize_german("Dönitz") == "Doenitz"
        assert _normalize_german("Müller") == "Mueller"
        assert _normalize_german("Größe") == "Groesse"
        assert _normalize_german("Straße") == "Strasse"

    def test_similarity_ratio(self):
        """Test name similarity calculation."""
        # Identical names
        assert _similarity_ratio("Eisenhower", "Eisenhower") == 1.0

        # Very similar
        ratio = _similarity_ratio("Dwight D. Eisenhower", "Dwight Eisenhower")
        assert ratio > 0.8

        # Unicode variants
        ratio = _similarity_ratio("Dönitz", "Donitz")
        assert ratio > 0.9

        # Different names
        ratio = _similarity_ratio("Eisenhower", "Patton")
        assert ratio < 0.5

    def test_extract_last_name(self):
        """Test last name extraction."""
        assert _extract_last_name("Dwight D. Eisenhower") == "Eisenhower"
        assert _extract_last_name("George S. Patton") == "Patton"
        assert _extract_last_name("Montgomery") == "Montgomery"
        assert _extract_last_name("von Rundstedt") == "Rundstedt"


class TestDuplicateDetection:
    """Test duplicate detection heuristics."""

    @pytest.fixture
    def sample_people(self):
        """Sample people data for testing."""
        return [
            {
                "filename": "Dwight_D_Eisenhower_01ABC.json",
                "name": "Dwight D. Eisenhower",
                "PersonID": "01ABC",
                "biographical_profile": {
                    "birth_date": "1890-10-14",
                    "nationality": "American",
                },
                "event_mentions": [{"position_at_event": "Supreme Commander"}],
            },
            {
                "filename": "Eisenhower_01DEF.json",
                "name": "Eisenhower",
                "PersonID": "01DEF",
                "biographical_profile": {},
                "event_mentions": [{"position_at_event": "Supreme Commander"}],
            },
            {
                "filename": "George_Patton_01GHI.json",
                "name": "George Patton",
                "PersonID": "01GHI",
                "biographical_profile": {
                    "nationality": "American",
                },
                "event_mentions": [],
            },
        ]

    def test_name_similarity_detection(self, sample_people):
        """Test detection based on name similarity."""
        person1 = sample_people[0]  # Dwight D. Eisenhower
        person2 = sample_people[1]  # Eisenhower

        # Should detect as potential duplicate
        ratio = _similarity_ratio(person1["name"], person2["name"])
        assert ratio > 0.6  # Threshold for flagging

    def test_shared_position_detection(self, sample_people):
        """Test detection based on shared positions."""
        person1 = sample_people[0]  # Dwight D. Eisenhower
        person2 = sample_people[1]  # Eisenhower

        # Both have "Supreme Commander" position
        positions1 = {m["position_at_event"] for m in person1["event_mentions"]}
        positions2 = {m["position_at_event"] for m in person2["event_mentions"]}

        assert len(positions1 & positions2) > 0  # Shared position

    def test_different_people_not_flagged(self, sample_people):
        """Test that different people are not flagged as duplicates."""
        person1 = sample_people[0]  # Eisenhower
        person3 = sample_people[2]  # Patton

        ratio = _similarity_ratio(person1["name"], person3["name"])
        assert ratio < 0.6  # Below threshold
