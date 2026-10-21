"""Regression — §1.0c WARNING: truncated-response cache eviction + batch dup-key visibility.

(1) grok_client._handle_truncation_error popped the poisoned cache entry ONLY in the <100K branch;
    a >100K truncated response (cached before parse) was never evicted, so a re-run cache-HIT and
    re-truncated forever (GrokTruncationError). Now BOTH branches evict.
(2) BatchCollector.add silently dropped a duplicate request_id(=cache_key); now it logs a WARNING so
    a stable-key collision (latent optional-entity-zero) is visible.
"""

import logging
from unittest import mock

import pytest

import src.grok_client as gc
from src.utils.batch_api import BatchCollector, BatchRequest


def _client_with_cache(cache: dict):
    c = gc.GrokClient.__new__(gc.GrokClient)
    c._get_cache = lambda ct: cache
    c._make_cache_key = lambda prompt, temp, model, sysp="": "KEY"
    c._get_model = lambda ct: "m"
    return c


@pytest.mark.parametrize("length", [50_000, 150_000])  # <100K and >100K
def test_truncation_evicts_cache_both_sizes(length):
    cache = {"KEY": "x" * length}  # poisoned truncated response cached pre-parse
    client = _client_with_cache(cache)
    with pytest.raises(gc.GrokTruncationError):
        client._handle_truncation_error(
            "x" * length, "Unterminated", "events", "prompt", 0.1
        )
    assert "KEY" not in cache  # evicted on BOTH <100K and >100K (the fix)


def test_batch_collector_logs_duplicate_request_id(caplog):
    c = BatchCollector()
    r1 = BatchRequest("samekey", [], "model", 0.1, "events")
    r2 = BatchRequest("samekey", [], "model", 0.1, "events")  # collides
    c.add(r1)
    with caplog.at_level(logging.WARNING):
        c.add(r2)
    assert len(c) == 1  # dup dropped (unchanged)
    assert any("duplicate_request_id_dropped" in r.message for r in caplog.records)


def test_truncation_evicts_the_stable_override_key():
    """The latent edge both reviewers flagged: eviction must target the SAME key chat_completion
    stored. When a stable cache_key override is passed, _handle_truncation_error must evict THAT key,
    not a 3-arg recompute."""
    cache = {
        "STABLE-OVERRIDE": "x" * 150_000
    }  # supplemental-style stable key, >100K truncated
    client = gc.GrokClient.__new__(gc.GrokClient)
    client._get_cache = lambda ct: cache
    # a DIFFERENT recompute than the override, to prove the override (not the recompute) is used
    client._make_cache_key = lambda *a: "RECOMPUTE-WOULD-MISS"
    client._get_model = lambda ct: "m"
    with pytest.raises(gc.GrokTruncationError):
        client._handle_truncation_error(
            "x" * 150_000,
            "Unterminated",
            "supplemental",
            "prompt",
            0.1,
            cache_key="STABLE-OVERRIDE",
        )
    assert "STABLE-OVERRIDE" not in cache  # the override entry was evicted (fix)
