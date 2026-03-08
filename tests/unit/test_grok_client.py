"""Unit tests for GrokClient."""

import json
from unittest.mock import Mock, patch

import pytest
import httpx

from src.grok_client import GrokClient


class TestGrokClient:
    """Test GrokClient functionality."""

    def test_init_with_cache_dir(self, tmp_path):
        """Test client initialization with cache directory."""
        cache_dir = tmp_path / "cache"
        client = GrokClient(cache_dir=cache_dir, api_key="test-key")

        assert client.cache_dir == cache_dir
        assert cache_dir.exists()

    def test_cache_hit(self, tmp_path):
        """Test that cached responses are returned."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Create cached response
        cache_file = cache_dir / "test_cache.json"
        cached_data = {"result": "cached"}
        cache_file.write_text(json.dumps(cached_data))

        client = GrokClient(cache_dir=cache_dir, api_key="test-key")

        # Mock the cache key generation
        with patch.object(client, "_get_cache_key", return_value="test_cache"):
            with patch.object(client, "_call_api") as mock_api:
                _ = client.chat_completion([{"role": "user", "content": "test"}])
                # API should not be called
                mock_api.assert_not_called()

    def test_api_error_handling(self, tmp_path):
        """Test API error handling."""
        client = GrokClient(cache_dir=tmp_path, api_key="test-key")

        with patch("httpx.post") as mock_post:
            mock_post.side_effect = httpx.HTTPStatusError(
                "500 Server Error", request=Mock(), response=Mock(status_code=500)
            )

            with pytest.raises(httpx.HTTPStatusError):
                # pylint: disable=protected-access
                client._call_api([{"role": "user", "content": "test"}])

    def test_extract_json_validation(self, tmp_path):
        """Test JSON extraction and validation."""
        client = GrokClient(cache_dir=tmp_path, api_key="test-key")

        mock_response = {
            "choices": [{"message": {"content": '{"name": "Test", "value": 123}'}}]
        }

        with patch.object(client, "_call_api", return_value=mock_response):
            result = client.extract_json("Extract data")
            assert result == {"name": "Test", "value": 123}

    def test_clear_cache(self, tmp_path):
        """Test cache clearing."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # Create some cache files
        (cache_dir / "events_test.json").write_text("{}")
        (cache_dir / "people_test.json").write_text("{}")

        client = GrokClient(cache_dir=cache_dir, api_key="test-key")
        client.clear_cache("events")

        assert not (cache_dir / "events_test.json").exists()
        assert (cache_dir / "people_test.json").exists()
