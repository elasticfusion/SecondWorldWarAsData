"""Integration tests for Phase 3 enrichment pipeline.

Tests the enrichment flow with mocked external APIs (Grokipedia, Wikipedia, Grok).
Verifies: enrichment status updates, data merge logic, skip-already-enriched behavior.
"""

# pylint: disable=missing-function-docstring,unused-argument

import json
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def people_dir(tmp_path):
    """Create a people directory with a sample person file."""
    d = tmp_path / "people"
    d.mkdir()
    (d / "index.json").write_text("{}", encoding="utf-8")
    return d


@pytest.fixture
def sample_person(people_dir):
    """Create a sample person file needing enrichment."""
    data = {
        "PersonID": "01TEST12345678901234AB",
        "name": "Omar N. Bradley",
        "biographical_profile": {},
        "event_mentions": [
            {
                "MentionID": "01MENT12345678901234AB",
                "Event_Name": "Lorraine Campaign",
                "EventID": "01EVNT12345678901234AB",
                "Sub_eventID": "01SEVT12345678901234AB",
            }
        ],
    }
    f = people_dir / "omar n bradley.json"
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f


@pytest.fixture
def enriched_person(people_dir):
    """Create a person file already enriched."""
    data = {
        "PersonID": "01ENRI12345678901234AB",
        "name": "Dwight D. Eisenhower",
        "enrichment_status": "enriched",
        "biographical_profile": {"nationality": "American"},
        "event_mentions": [],
    }
    f = people_dir / "eisenhower.json"
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f


@pytest.fixture
def mock_grok():
    """Mock GrokClient that returns canned biography synthesis."""
    client = Mock(
        spec=["chat_completion", "batch_mode", "_batch_collector", "log_cache_stats"]
    )
    client.batch_mode = False
    client._batch_collector = None
    client.chat_completion = Mock(
        return_value=json.dumps(
            {
                "birth_date": "1893-02-12",
                "nationality": "American",
                "role_type": "military_leader",
                "units_served": ["12th Army Group"],
            }
        )
    )
    return client


class TestEnrichPersonBiography:
    def test_enriches_unenriched_person(self, sample_person, mock_grok):
        from src.extraction.enrich_biographies import enrich_person_biography

        with (
            patch(
                "src.extraction.enrich_biographies.search_grokipedia",
                return_value="Omar Bradley was a US Army general...",
            ),
            patch(
                "src.extraction.enrich_biographies.search_wikipedia",
                return_value=None,
            ),
            patch(
                "src.extraction.enrich_biographies._validate_and_store_urls",
                return_value=False,
            ),
        ):
            enrich_person_biography(
                sample_person, mock_grok, search_references_flag=False
            )

        # File should be updated with enrichment status
        data = json.loads(sample_person.read_text(encoding="utf-8"))
        assert data.get("enrichment_status") in ("enriched", "not_found")

    def test_skips_already_enriched(self, enriched_person, mock_grok):
        from src.extraction.enrich_biographies import enrich_person_biography

        result = enrich_person_biography(enriched_person, mock_grok)
        assert result is False
        # Grok should NOT have been called
        assert not mock_grok.chat_completion.called

    def test_marks_not_found_when_no_sources(self, sample_person, mock_grok):
        from src.extraction.enrich_biographies import enrich_person_biography

        with (
            patch(
                "src.extraction.enrich_biographies.search_grokipedia",
                return_value=None,
            ),
            patch(
                "src.extraction.enrich_biographies.search_wikipedia",
                return_value=None,
            ),
        ):
            result = enrich_person_biography(sample_person, mock_grok)

        assert result is False
        data = json.loads(sample_person.read_text(encoding="utf-8"))
        assert data["enrichment_status"] == "not_found"


class TestEnrichAllPeople:
    def test_skips_already_enriched_in_bulk(
        self, people_dir, enriched_person, mock_grok
    ):
        from src.extraction.enrich_biographies import enrich_all_people

        with (
            patch(
                "src.extraction.enrich_biographies.search_grokipedia",
                return_value=None,
            ),
            patch(
                "src.extraction.enrich_biographies.search_wikipedia",
                return_value=None,
            ),
        ):
            enriched = enrich_all_people(people_dir, mock_grok, max_workers=1)

        assert enriched == 0  # Already enriched, nothing new

    def test_respects_max_people(self, people_dir, mock_grok):
        from src.extraction.enrich_biographies import enrich_all_people

        # Create 5 people files
        for i in range(5):
            data = {"PersonID": f"01TEST{i:020d}", "name": f"Person {i}"}
            (people_dir / f"person_{i}.json").write_text(
                json.dumps(data), encoding="utf-8"
            )

        with (
            patch(
                "src.extraction.enrich_biographies.search_grokipedia",
                return_value=None,
            ),
            patch(
                "src.extraction.enrich_biographies.search_wikipedia",
                return_value=None,
            ),
        ):
            enrich_all_people(people_dir, mock_grok, max_people=2, max_workers=1)

        # Only 2 should have been processed (marked not_found)
        processed = sum(
            1
            for f in people_dir.glob("person_*.json")
            if json.loads(f.read_text(encoding="utf-8")).get("enrichment_status")
        )
        assert processed == 2
