"""OBSERV cachekey traceability: log key + per-input fingerprints, fail-safe."""

import logging
from unittest.mock import MagicMock


def _client():
    from src.grok_client import GrokClient

    c = GrokClient.__new__(GrokClient)  # bypass heavy __init__
    c.model = "grok-4.3"
    return c


def test_observe_cache_key_emits_fingerprints(caplog):
    c = _client()
    with caplog.at_level(logging.INFO):
        c._observe_cache_key(
            "submit",
            "supplemental",
            "deadbeef" * 8,
            "grok-4.3",
            0.1,
            "sys prompt",
            "user prompt",
        )
    line = [r.message for r in caplog.records if "OBSERV cachekey" in r.message]
    assert line, "no OBSERV cachekey line emitted"
    msg = line[0]
    assert "cachekey.submit" in msg and "type=supplemental" in msg
    # fingerprints are len:sha8, not raw content
    assert "sys=" in msg and "prompt=" in msg and "sys prompt" not in msg


def test_observe_cache_key_failsafe():
    c = _client()
    # Pass a non-str prompt -> _fp would raise inside; must be swallowed (no exception).
    c._observe_cache_key("lookup", "x", "k", "m", 0.1, "s", 12345)  # type: ignore[arg-type]
