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

    failed = client._map_ordered_results(submitted, ordered, metrics, {}, "b1")

    assert failed == []  # NOT routed to realtime retry
    assert metrics.valid == 1
    assert (
        client.caches["events"]["ourkey-events-1"] == '{"ok":1}'
    )  # cached under our key
