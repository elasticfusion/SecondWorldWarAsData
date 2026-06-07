"""Tests for resolve_surname_people.py and resolve_title_people.py."""

# pylint: disable=missing-function-docstring

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def people_dir(tmp_path):
    """Create output/people with sample files."""
    d = tmp_path / "output" / "people"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def surname_person(people_dir):
    """A single-word name person file."""
    data = {
        "PersonID": "01TEST",
        "name": "Bradley",
        "rank": "General",
        "nationality": "American",
        "event_mentions": [
            {
                "original_text": "General Omar N. Bradley commanded the 12th Army Group",
                "position_at_event": "Commander, 12th Army Group",
            }
        ],
    }
    f = people_dir / "bradley.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    return f


@pytest.fixture
def title_person(people_dir):
    """A title-as-name person file."""
    data = {
        "PersonID": "01TITLE",
        "name": "Commander of Third Army",
        "event_mentions": [
            {
                "original_text": "The Commander of Third Army ordered the advance",
                "context": "Lorraine Campaign September 1944",
                "book": "The Lorraine Campaign",
            }
        ],
    }
    f = people_dir / "commander of third army.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    return f


@pytest.fixture
def mock_grok():
    client = Mock()
    client.chat_completion = Mock(return_value="Omar N. Bradley")
    return client


class TestResolveSurnamePeople:
    def test_knowledge_lookup_returns_full_name(self, surname_person, mock_grok):
        from scripts.resolve_surname_people import _knowledge_lookup

        data = json.loads(surname_person.read_text(encoding="utf-8"))
        result = _knowledge_lookup(data, "Bradley", mock_grok)

        assert result == "Omar N. Bradley"
        mock_grok.chat_completion.assert_called_once()
        call_kwargs = mock_grok.chat_completion.call_args[1]
        assert "Bradley" in call_kwargs["prompt"]
        assert call_kwargs["temperature"] == 0.0

    def test_knowledge_lookup_returns_surname_when_no_context(self, mock_grok):
        from scripts.resolve_surname_people import _knowledge_lookup

        entry = {"name": "Smith", "event_mentions": []}
        result = _knowledge_lookup(entry, "Smith", mock_grok)

        assert result == "Smith"
        mock_grok.chat_completion.assert_not_called()

    def test_knowledge_lookup_strips_quotes_and_newlines(self, mock_grok):
        from scripts.resolve_surname_people import _knowledge_lookup

        mock_grok.chat_completion.return_value = "Paul W. Baade\nsome extra"
        entry = {
            "name": "Baade",
            "rank": "General",
            "event_mentions": [{"position_at_event": "CO 35th Div"}],
        }
        result = _knowledge_lookup(entry, "Baade", mock_grok)
        assert result == "Paul W. Baade"


class TestResolveTitlePeople:
    def test_find_title_people(self, people_dir, title_person):
        from scripts.resolve_title_people import find_title_people

        with patch("glob.glob", return_value=[str(title_person)]):
            candidates = find_title_people()

        assert len(candidates) == 1
        assert candidates[0][1]["name"] == "Commander of Third Army"

    def test_find_title_people_skips_normal_names(self, people_dir):
        from scripts.resolve_title_people import find_title_people

        normal = people_dir / "eisenhower.json"
        normal.write_text(json.dumps({"name": "Dwight Eisenhower"}), encoding="utf-8")

        with patch("glob.glob", return_value=[str(normal)]):
            candidates = find_title_people()

        assert len(candidates) == 0

    def test_resolve_person_returns_result_on_high_confidence(self, mock_grok):
        from scripts.resolve_title_people import resolve_person

        mock_grok.chat_completion.return_value = json.dumps(
            {"name": "George S. Patton", "confidence": 0.95, "source": "Known fact"}
        )
        entry = {
            "name": "Commander of Third Army",
            "event_mentions": [
                {"context": "Lorraine 1944", "book": "The Lorraine Campaign"}
            ],
        }
        result = resolve_person(entry, mock_grok)

        assert result["name"] == "George S. Patton"
        assert result["confidence"] == 0.95

    def test_resolve_person_returns_none_on_low_confidence(self, mock_grok):
        from scripts.resolve_title_people import resolve_person

        mock_grok.chat_completion.return_value = json.dumps(
            {"name": "Someone", "confidence": 0.3, "source": "guess"}
        )
        entry = {"name": "Officer", "event_mentions": []}
        result = resolve_person(entry, mock_grok)

        assert result is None

    def test_resolve_person_returns_none_on_invalid_json(self, mock_grok):
        from scripts.resolve_title_people import resolve_person

        mock_grok.chat_completion.return_value = "I don't know"
        entry = {"name": "Director of supply", "event_mentions": []}
        result = resolve_person(entry, mock_grok)

        assert result is None
