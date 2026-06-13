"""Unit tests for GrokClient."""

import json
from unittest.mock import Mock, patch

import pytest
import requests

from src.grok_client import GrokClient


class TestGrokClient:
    """Test GrokClient functionality."""

    def test_init_with_cache_dir(self, tmp_path):
        """Test client initialization with cache directory."""
        cache_dir = tmp_path / "cache"
        client = GrokClient(cache_dir=cache_dir, api_key="test-key")

        assert client.cache_dir == cache_dir
        # Cache dir is created lazily when first used
        assert client.api_key == "test-key"

    def test_cache_hit(self, tmp_path):
        """Test that cached responses are returned."""
        cache_dir = tmp_path / "cache"
        client = GrokClient(cache_dir=cache_dir, api_key="test-key")

        # Manually populate cache
        cache = client._get_cache("test_type")
        cache_key = client._make_cache_key("test prompt", 0.1)
        cache[cache_key] = "cached response"

        # Mock _call_api to ensure it's not called
        with patch.object(client, "_call_api") as mock_api:
            result = client.chat_completion(
                "test prompt", temperature=0.1, cache_type="test_type"
            )
            assert result == "cached response"
            mock_api.assert_not_called()

    def test_api_error_handling(self, tmp_path):
        """Test API error handling."""
        client = GrokClient(cache_dir=tmp_path, api_key="test-key")

        with patch("src.utils.http_pool.get_session") as mock_session:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_response.text = '{"error": "Server error"}'
            mock_response.raise_for_status.side_effect = requests.HTTPError(
                "500 Server Error"
            )
            mock_session.return_value.__enter__.return_value.post.return_value = (
                mock_response
            )

            with pytest.raises(Exception):  # Will raise GrokAPIError or HTTPError
                # pylint: disable=protected-access
                client._call_api([{"role": "user", "content": "test"}])

    def test_extract_json_validation(self, tmp_path):
        """Test JSON extraction and validation."""
        client = GrokClient(cache_dir=tmp_path, api_key="test-key")

        # Mock chat_completion to return JSON string
        with patch.object(
            client, "chat_completion", return_value='{"name": "Test", "value": 123}'
        ):
            result = client.extract_json("Extract data")
            assert result == {"name": "Test", "value": 123}

    def test_clear_cache(self, tmp_path):
        """Test cache clearing."""
        cache_dir = tmp_path / "cache"
        client = GrokClient(cache_dir=cache_dir, api_key="test-key")

        # Create caches with data
        events_cache = client._get_cache("events")
        people_cache = client._get_cache("people")

        events_cache["key1"] = "value1"
        people_cache["key2"] = "value2"

        # Clear events cache
        events_cache.clear()

        assert "key1" not in events_cache
        assert "key2" in people_cache


class TestValidatePrompt:
    """Test input validation before sending to API."""

    def test_empty_prompt_raises(self, tmp_path):
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        with pytest.raises(ValueError, match="Empty prompt"):
            client._validate_prompt("")

    def test_whitespace_only_raises(self, tmp_path):
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        with pytest.raises(ValueError, match="Empty prompt"):
            client._validate_prompt("   \n  ")

    def test_unfilled_placeholders_raises(self, tmp_path):
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        with pytest.raises(ValueError, match="unfilled placeholders.*book"):
            client._validate_prompt("Extract events from {book} by {author}")

    def test_json_braces_not_flagged(self, tmp_path):
        """JSON content with braces should not trigger placeholder detection."""
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        prompt = 'Return JSON: {"EventID": "abc", "Sub-events": []}'
        # Should not raise
        client._validate_prompt(prompt)

    def test_valid_prompt_passes(self, tmp_path):
        client = GrokClient(cache_dir=tmp_path / "cache", api_key="test-key")
        client._validate_prompt(
            "Extract events from The Lorraine Campaign by Hugh Cole"
        )
