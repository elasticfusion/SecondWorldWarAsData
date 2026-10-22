"""Regression: BatchResult valid-gate is content-first (re-review HIGH).

Before: status 'valid' required finish_reason=='stop', but retrieve_results_ordered defaults
finish_reason to 'unknown' when the xAI API omits it (and providers vary the terminal token). A
completed choice WITH content but a non-'stop' reason was classified 'other_finish' -> discarded ->
re-billed via realtime retry -> counted as a partial failure. Now: content-bearing results are valid
unless genuinely truncated ('length') or errored/empty.
"""

from unittest import mock


def test_batchresult_status_content_first():
    from src.utils.batch_api import BatchResult

    # content + non-'stop' reasons the API/providers actually emit -> VALID (was discarded)
    for fr in ("stop", "unknown", "", "end_turn", "eos"):
        r = BatchResult("r", content='{"ok":1}', finish_reason=fr)
        assert r.status == "valid", fr
        assert r.ok is True, fr
    # genuine truncation -> truncated (so chunk-halving still triggers)
    r = BatchResult("r", content='{"part', finish_reason="length")
    assert r.status == "truncated" and r.truncated and not r.ok
    # error set -> error
    r = BatchResult("r", content='{"ok":1}', finish_reason="stop", error="boom")
    assert r.status == "error" and not r.ok
    # no content -> error (even with a terminal reason)
    r = BatchResult("r", content="", finish_reason="unknown")
    assert r.status == "error" and not r.ok


def test_map_ordered_results_caches_unknown_finish_reason_content():
    """Integration: a content-bearing result with finish_reason='unknown' (the ordered-retrieve
    default) is now CACHED under our key, not routed to realtime retry / counted failed.
    """
    import src.grok_client as gc
    from src.utils.batch_api import BatchResult, BatchMetrics

    client = gc.GrokClient.__new__(gc.GrokClient)
    client.caches = {}
    client._cache_lock = __import__("threading").Lock()
    client._get_cache = lambda ct: client.caches.setdefault(ct, {})
    client._sanitize_content = lambda c: c
    client._batch_prompt_preview = lambda rid, rbi: ""

    submitted = [("ourkey-events-1", "events")]
    ordered = [BatchResult("srv-1", content='{"ok":1}', finish_reason="unknown")]
    metrics = BatchMetrics(batch_id="b1", total_requests=1)

    failed, count_mismatch = client._map_ordered_results(
        submitted, ordered, metrics, {}, "b1"
    )

    assert failed == []  # NOT routed to realtime retry
    assert count_mismatch is False  # counts matched
    assert metrics.valid == 1
    assert (
        client.caches["events"]["ourkey-events-1"] == '{"ok":1}'
    )  # cached under our key


def test_count_mismatch_signals_and_forces_retry():
    """§1.0d WARNING: a result-count mismatch must set count_mismatch=True and FORCE real-time
    retry past the 20%-systemic guard — otherwise a single dropped/extra results page zeros the
    whole batch's extraction with no recovery."""
    import src.grok_client as gc
    from src.utils.batch_api import BatchResult, BatchMetrics

    client = gc.GrokClient.__new__(gc.GrokClient)
    client.caches = {}
    client._cache_lock = __import__("threading").Lock()
    client._get_cache = lambda ct: client.caches.setdefault(ct, {})
    client._sanitize_content = lambda c: c
    client._batch_prompt_preview = lambda rid, rbi: ""

    # 2 submitted, only 1 result returned (the API dropped a page) -> count mismatch.
    submitted = [("k1", "events"), ("k2", "events")]
    ordered = [BatchResult("srv-1", content='{"ok":1}', finish_reason="stop")]
    metrics = BatchMetrics(batch_id="b1", total_requests=2)

    failed, count_mismatch = client._map_ordered_results(
        submitted, ordered, metrics, {}, "b1"
    )
    assert count_mismatch is True
    assert sorted(failed) == ["k1", "k2"]  # all marked failed (can't positionally map)


def test_count_mismatch_force_bypasses_systemic_guard():
    """With force=True (count mismatch), _retry_failed_batch retries even at 100% failure instead
    of hitting the >20% 'systemic — skip retry' branch."""
    import src.grok_client as gc
    from src.utils.batch_api import BatchMetrics

    client = gc.GrokClient.__new__(gc.GrokClient)
    client.caches = {}
    client._cache_lock = __import__("threading").Lock()
    client._get_cache = lambda ct: client.caches.setdefault(ct, {})
    client._sanitize_content = lambda c: c
    client._batch_meta = {"k1": "events", "k2": "events"}

    class _Req:
        messages = [{"role": "user", "content": "x"}]
        temperature = 0.1

    requests_by_id = {"k1": _Req(), "k2": _Req()}
    calls = []
    client._call_api = lambda msgs, temp: calls.append(1) or {
        "choices": [{"message": {"content": '{"ok":1}'}}]
    }
    metrics = BatchMetrics(batch_id="b1", total_requests=2)

    # 100% failed but force=True -> must retry both (not skip as 'systemic').
    client._retry_failed_batch(["k1", "k2"], requests_by_id, metrics, 2, force=True)
    assert len(calls) == 2  # both retried via real-time API
    assert metrics.retry_recovered == 2

    # Control: WITHOUT force, 100% failure trips the systemic guard (no retries).
    calls.clear()
    m2 = BatchMetrics(batch_id="b2", total_requests=2)
    client._retry_failed_batch(["k1", "k2"], requests_by_id, m2, 2, force=False)
    assert calls == []  # skipped as systemic
