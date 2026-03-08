"""Integration tests for Phase 2 extraction pipeline."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from src.extraction.people import extract_people


class TestPhase2Integration:
    """Integration tests for Phase 2 extraction."""

    def test_people_extraction_end_to_end(
        self, mock_grok_client, sample_parsed_chapter, temp_output_dir
    ):
        """Test complete people extraction workflow."""
        # Mock Grok response
        mock_response = {
            "people": [
                {
                    "PersonID": "01H8XYZ123",
                    "name": "Dwight D. Eisenhower",
                    "source_language": "English",
                    "aliases": [],
                    "biographical_profile": {
                        "nationality": "American",
                        "role_type": "military_leader",
                    },
                    "event_mentions": [],
                }
            ]
        }

        mock_grok_client.extract_structured.return_value = Mock(
            model_dump=lambda: mock_response
        )

        # Run extraction
        with patch("src.extraction.people.GrokClient", return_value=mock_grok_client):
            extract_people(
                event_file=sample_parsed_chapter,
                grok_client=mock_grok_client,
                output_dir=temp_output_dir,
            )

        # Verify output
        people_dir = temp_output_dir / "people"
        index_file = people_dir / "index.json"

        assert index_file.exists()

        with open(index_file) as f:
            index = json.load(f)
            assert "dwight d eisenhower" in index

        # Verify person file exists
        person_files = list(people_dir.glob("Dwight_D_Eisenhower_*.json"))
        assert len(person_files) == 1

    def test_incremental_extraction(self, temp_output_dir, mock_grok_client, tmp_path):
        """Test that extraction accumulates across multiple chapters."""
        import json
        
        people_dir = temp_output_dir / "people"
        people_dir.mkdir(exist_ok=True)

        # First extraction
        person1 = {
            "PersonID": "01H8XYZ123",
            "name": "Dwight D. Eisenhower",
            "source_language": "English",
            "aliases": [],
            "biographical_profile": {},
            "event_mentions": [{"MentionID": "01AAA", "Event_Name": "D-Day"}],
        }

        person_file = people_dir / "Dwight_D_Eisenhower_01H8XYZ123.json"
        person_file.write_text(json.dumps(person1))

        # Second extraction with same person
        mock_response = {
            "people": [
                {
                    "PersonID": "01H8XYZ123",
                    "name": "Dwight D. Eisenhower",
                    "source_language": "English",
                    "aliases": ["Ike"],
                    "biographical_profile": {},
                    "event_mentions": [
                        {"MentionID": "01BBB", "Event_Name": "Operation Overlord"}
                    ],
                }
            ]
        }

        mock_grok_client.extract_structured.return_value = Mock(
            model_dump=lambda: mock_response
        )
        
        # Create dummy event file
        event_file = tmp_path / "test-event.json"
        event_data = {
            "Chapter": "Test",
            "Event": {
                "EventID": "01TEST",
                "Sub-events": [
                    {
                        "Sub-eventID": "01TESTSUB",
                        "Sub-event_summary": "Test",
                        "Sub-event_fulltext": {"1": "Test text"},
                    }
                ],
            },
        }
        with open(event_file, "w") as f:
            json.dump(event_data, f)

        with patch("src.extraction.people.GrokClient", return_value=mock_grok_client):
            extract_people(
                event_file=event_file,
                grok_client=mock_grok_client,
                output_dir=temp_output_dir,
            )

        # Verify merge
        with open(person_file) as f:
            merged = json.load(f)
            assert "Ike" in merged["aliases"]
            assert len(merged["event_mentions"]) == 2
