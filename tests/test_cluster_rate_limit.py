"""Tests for cluster-wide Grok rate limiting (M4 sub-part 1, spec §5.2).

Under multi-doc concurrency, Grok's ACCOUNT-WIDE rate limit must be divided
across the task pool so N parallel tasks don't collectively exceed it. At pool=1
(or multi_doc off) the per-task budget is byte-identical to today's configured
value — the §15 migration invariant.
"""

from unittest.mock import patch

from src.grok_client import _effective_calls_per_minute


def test_serial_mode_unchanged():
    """multi_doc off → configured value, no division."""
    cfg = {"api": {"calls_per_minute": 30}}
    assert _effective_calls_per_minute(cfg) == 30


def test_multi_doc_divides_by_pool_max():
    """multi_doc on → calls_per_minute // pool_max."""
    cfg = {
        "api": {"calls_per_minute": 30},
        "concurrency": {"multi_doc": {"enabled": True, "pool_max": 6}},
    }
    assert _effective_calls_per_minute(cfg) == 5  # 30 // 6


def test_multi_doc_pool_one_is_unchanged():
    cfg = {
        "api": {"calls_per_minute": 30},
        "concurrency": {"multi_doc": {"enabled": True, "pool_max": 1}},
    }
    assert _effective_calls_per_minute(cfg) == 30


def test_env_override_takes_precedence():
    """The dispatcher can inject the CURRENT adaptive pool size via env."""
    cfg = {
        "api": {"calls_per_minute": 60},
        "concurrency": {"multi_doc": {"enabled": True, "pool_max": 10}},
    }
    with patch.dict("os.environ", {"GROK_RATE_POOL_SIZE": "4"}):
        assert _effective_calls_per_minute(cfg) == 15  # 60 // 4, not // 10


def test_floor_at_one_per_minute():
    """Never drop below 1/min even if pool > rate."""
    cfg = {
        "api": {"calls_per_minute": 3},
        "concurrency": {"multi_doc": {"enabled": True, "pool_max": 10}},
    }
    assert _effective_calls_per_minute(cfg) == 1


def test_default_calls_per_minute_when_missing():
    assert _effective_calls_per_minute({}) == 30


def test_multi_doc_enabled_missing_pool_max_defaults_one():
    cfg = {
        "api": {"calls_per_minute": 30},
        "concurrency": {"multi_doc": {"enabled": True}},
    }
    # pool_max defaults to 1 → unchanged
    assert _effective_calls_per_minute(cfg) == 30
