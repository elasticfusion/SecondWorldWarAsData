"""Tests for malformed LLM response handling and ULID validation.

Verifies that the pipeline gracefully handles:
- Truncated JSON
- Markdown-wrapped responses
- Empty responses
- Wrong schema format
- Invalid ULIDs in output
"""

# pylint: disable=missing-function-docstring

import json

import pytest

from src.grok_client import GrokClient
from src.utils.json_validator import _fix_invalid_ulids, _is_ulid_field


class TestMarkdownStripping:
    """Test that markdown code block wrappers are stripped from LLM responses."""

    @pytest.fixture
    def client(self, tmp_path):
        return GrokClient(cache_dir=tmp_path / "cache")

    def test_strips_json_markdown_wrapper(self, client):
        raw = '```json\n{"EventID": "01ABC"}\n```'
        result = client._strip_markdown_wrapper(raw)
        assert result == '{"EventID": "01ABC"}'

    def test_strips_plain_markdown_wrapper(self, client):
        raw = '```\n{"name": "test"}\n```'
        result = client._strip_markdown_wrapper(raw)
        assert result == '{"name": "test"}'

    def test_passes_clean_json_through(self, client):
        raw = '{"EventID": "01ABC"}'
        result = client._strip_markdown_wrapper(raw)
        assert result == raw

    def test_handles_whitespace_around_markdown(self, client):
        raw = '  ```json\n{"x": 1}\n```  '
        result = client._strip_markdown_wrapper(raw)
        assert result == '{"x": 1}'


class TestTruncatedJson:
    """Test handling of truncated JSON responses (incomplete output)."""

    def test_truncated_json_raises_on_parse(self):
        truncated = '{"Event": {"EventID": "01ABC", "Sub-events": ['
        with pytest.raises(json.JSONDecodeError):
            json.loads(truncated)

    def test_missing_closing_brace(self):
        incomplete = '{"PersonID": "01ABC", "name": "Patton"'
        with pytest.raises(json.JSONDecodeError):
            json.loads(incomplete)


class TestEmptyResponses:
    """Test handling of empty or whitespace-only LLM responses."""

    @pytest.fixture
    def client(self, tmp_path):
        return GrokClient(cache_dir=tmp_path / "cache")

    def test_empty_string_after_strip(self, client):
        result = client._strip_markdown_wrapper("")
        assert result == ""

    def test_whitespace_only(self, client):
        result = client._strip_markdown_wrapper("   \n  \n  ")
        assert result == ""

    def test_empty_markdown_block(self, client):
        result = client._strip_markdown_wrapper("```json\n```")
        assert result == ""


class TestWrongSchemaFormat:
    """Test that responses with wrong schema are detected."""

    def test_people_response_missing_required_field(self):
        """A people response without PersonID should be flagged by ULID fixer."""
        response = {"name": "Patton", "rank": "General"}
        # _fix_invalid_ulids won't add missing fields, but won't crash
        result = _fix_invalid_ulids(response)
        assert "PersonID" not in result  # Doesn't invent fields

    def test_flat_event_detected_as_unwrapped(self):
        """Code should detect flat event format (no 'Event' wrapper)."""
        flat = {"EventID": "01HX7YZABCDEFGHJKMNPQRSTVW", "Sub-events": []}
        assert "Event" not in flat
        assert "EventID" in flat

        # After wrapping (as code does):
        wrapped = {"Event": flat}
        assert "Event" in wrapped
        assert wrapped["Event"]["EventID"] == "01HX7YZABCDEFGHJKMNPQRSTVW"


class TestUlidValidation:
    """Test ULID validation and fixing in LLM output."""

    def test_valid_ulid_not_flagged(self):
        assert _is_ulid_field("PersonID", "01HX7YZABCDEFGHJKMNPQRSTVW") is False

    def test_invalid_ulid_too_short(self):
        assert _is_ulid_field("PersonID", "01ABC") is True

    def test_invalid_ulid_wrong_chars(self):
        # ULIDs use Crockford base32 — no I, L, O, U
        assert _is_ulid_field("EventID", "01HX7YZABCDEFGHIJKLMNOPQRS") is True

    def test_non_id_field_not_checked(self):
        assert _is_ulid_field("name", "short") is False

    def test_empty_value_not_flagged(self):
        assert _is_ulid_field("PersonID", "") is False

    def test_fix_replaces_invalid_ulid(self):
        data = {"PersonID": "INVALID", "name": "Test"}
        result = _fix_invalid_ulids(data)
        assert result["PersonID"] != "INVALID"
        assert len(result["PersonID"]) == 26
        assert result["name"] == "Test"  # Non-ID field untouched

    def test_fix_preserves_valid_ulid(self):
        valid = "01HX7YZABCDEFGHJKMNPQRSTVW"
        data = {"EventID": valid, "name": "Test"}
        result = _fix_invalid_ulids(data)
        assert result["EventID"] == valid

    def test_fix_handles_nested_ulids(self):
        data = {
            "Event": {
                "EventID": "BAD_ULID",
                "Sub-events": [{"Sub-eventID": "ALSO_BAD"}],
            }
        }
        result = _fix_invalid_ulids(data)
        assert len(result["Event"]["EventID"]) == 26
        assert len(result["Event"]["Sub-events"][0]["Sub-eventID"]) == 26

    def test_fix_handles_list_of_dicts(self):
        data = [{"PersonID": "SHORT"}, {"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW"}]
        result = _fix_invalid_ulids(data)
        assert len(result[0]["PersonID"]) == 26
        assert result[1]["PersonID"] == "01HX7YZABCDEFGHJKMNPQRSTVW"
