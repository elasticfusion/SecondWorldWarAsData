"""GROK_CACHE_DISABLED kill-switch: when set, Grok cache reads AND writes are suppressed
(every request hits the API fresh, nothing stale served, nothing new written)."""

import os
from unittest.mock import MagicMock, patch
import pytest
from src.grok_client import _cache_globally_disabled


@pytest.mark.parametrize(
    "val,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("off", False),
    ],
)
def test_killswitch_env_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("GROK_CACHE_DISABLED", val)
    assert _cache_globally_disabled() is expected


def test_killswitch_unset_is_false(monkeypatch):
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    assert _cache_globally_disabled() is False


def _make_client():
    from src.grok_client import GrokClient

    c = GrokClient.__new__(GrokClient)
    return c


def test_flag_set_suppresses_read_and_write(monkeypatch):
    """With the flag set, extract_json must NOT read from nor write to the cache,
    and must call the API even if a matching cache entry exists."""
    monkeypatch.setenv("GROK_CACHE_DISABLED", "1")
    from src.grok_client import GrokClient

    c = GrokClient.__new__(GrokClient)
    # a cache that ALREADY contains the key — must be ignored on read + not written
    fake_cache = {}

    class SpyCache(dict):
        writes = 0

        def __contains__(self, k):
            return dict.__contains__(self, k)

        def __setitem__(self, k, v):
            SpyCache.writes += 1
            dict.__setitem__(self, k, v)

    sc = SpyCache()
    dict.__setitem__(sc, "K", "STALE_CACHED_VALUE")  # seed without counting as a write
    SpyCache.writes = 0
    c._get_cache = MagicMock(return_value=sc)
    c._get_model = MagicMock(return_value="grok-4.3")
    c._make_cache_key = MagicMock(return_value="K")
    c._observe_cache_key = MagicMock()
    c._log_api_call = MagicMock()
    c._cache_lock = __import__("threading").Lock()
    c._cache_misses = 0
    c.batch_mode = False
    c._call_api = MagicMock(
        return_value={"choices": [{"message": {"content": "FRESH_FROM_API"}}]}
    )
    c._sanitize_completion_content = lambda x: x
    out = c.chat_completion("prompt", cache_type="equipment", cache_key="K")
    assert out == "FRESH_FROM_API"  # did NOT return the stale cached value
    assert c._call_api.called  # hit the API
    assert SpyCache.writes == 0  # did NOT write back to cache
