"""Regression: positional-result-mapping desync guard (HIGH, XChannel-8 2026-10-30).

The xAI batch RESULTS API does NOT echo our submitted ``custom_id`` — each result carries only a
server ``batch_request_id`` — so ``_map_ordered_results`` maps results to our submitted
(request_id, cache_type) PURELY BY POSITION via ``zip(submitted, ordered)``. A count-mismatch guard
already exists, but counts can MATCH while a positional slot maps to an EMPTY/too-short result and
that entity's real response sits uncorrelated elsewhere — silently persisting 0 for a whole entity
(places=0 while a valid 119-place response was cached unmapped), with NO error.

This guard detects the desync SIGNATURE — an empty/too-short result in a batch where SIBLINGS
returned substantive content — and:
  (a) adds the entity to ``failed_ids`` so the caller FORCES a real-time retry, and
  (b) emits a LOUD ``observe_error('batch', 'result_unmapped')``.
It must NOT false-retry a LEGITIMATELY empty batch (model genuinely returned ``{}`` because there
were no entities): an ALL-empty batch is left as-is. Only a MIX of empty+substantive is suspect.
"""

from unittest import mock


def _make_client():
    """Build a GrokClient with just the attrs _map_ordered_results touches (mirrors the pattern in
    tests/test_batchresult_valid_gate.py)."""
    import src.grok_client as gc

    client = gc.GrokClient.__new__(gc.GrokClient)
    client.caches = {}
    client._cache_lock = __import__("threading").Lock()
    client._get_cache = lambda ct: client.caches.setdefault(ct, {})
    client._sanitize_content = lambda c: c
    client._batch_prompt_preview = lambda rid, rbi: ""
    return client


def test_mixed_empty_and_substantive_forces_retry_and_observes():
    """(a) One slot maps to '{}' while a sibling returns substantive JSON -> the empty one is added
    to failed_ids (forced real-time retry) AND observe_error('batch','result_unmapped') fires.
    """
    import src.grok_client as gc
    from src.utils.batch_api import BatchResult, BatchMetrics

    client = _make_client()

    # k1 gets the DESYNC artefact ('{}' valid-but-empty); k2 got real content.
    submitted = [("k1", "places"), ("k2", "places")]
    ordered = [
        BatchResult("srv-1", content="{}", finish_reason="stop"),
        BatchResult(
            "srv-2", content='{"places":[{"name":"Bastogne"}]}', finish_reason="stop"
        ),
    ]
    metrics = BatchMetrics(batch_id="b1", total_requests=2)

    with mock.patch.object(gc, "observe_error") as obs:
        failed, count_mismatch = client._map_ordered_results(
            submitted, ordered, metrics, {}, "b1"
        )

    assert count_mismatch is False  # counts matched — this is the UNGUARDED path
    assert failed == ["k1"]  # the empty slot is forced to real-time retry
    # k1 was NOT silently persisted as empty...
    assert "k1" not in client.caches.get("places", {})
    # ...while the substantive sibling WAS cached.
    assert client.caches["places"]["k2"] == '{"places":[{"name":"Bastogne"}]}'
    assert metrics.valid == 1
    assert metrics.empty == 1

    # LOUD canonical error fired with the right scope/event + identifying fields.
    obs.assert_called_once()
    args, kwargs = obs.call_args
    assert args[0] == "batch"
    assert args[1] == "result_unmapped"
    assert kwargs["request_id"] == "k1"
    assert kwargs["cache_type"] == "places"
    assert "forcing real-time retry" in kwargs["note"]


def test_all_empty_batch_is_genuine_no_false_retry():
    """(b) An ALL-empty batch (every slot '{}'/'[]') is GENUINE (model found no entities) -> NONE
    forced to retry, NO observe_error. Empties are cached as the real answer."""
    import src.grok_client as gc
    from src.utils.batch_api import BatchResult, BatchMetrics

    client = _make_client()

    submitted = [("k1", "places"), ("k2", "people"), ("k3", "events")]
    ordered = [
        BatchResult("srv-1", content="{}", finish_reason="stop"),
        BatchResult("srv-2", content="[]", finish_reason="stop"),
        BatchResult("srv-3", content="{}", finish_reason="unknown"),
    ]
    metrics = BatchMetrics(batch_id="b2", total_requests=3)

    with mock.patch.object(gc, "observe_error") as obs:
        failed, count_mismatch = client._map_ordered_results(
            submitted, ordered, metrics, {}, "b2"
        )

    assert count_mismatch is False
    assert failed == []  # genuine-empty: no false retry
    obs.assert_not_called()  # no loud error for a genuine empty batch
    # Genuine empties cached as the valid answer under our keys.
    assert client.caches["places"]["k1"] == "{}"
    assert client.caches["people"]["k2"] == "[]"
    assert client.caches["events"]["k3"] == "{}"
    assert metrics.valid == 3
    assert metrics.empty == 0


def test_all_substantive_batch_none_failed():
    """(c) All slots substantive -> NONE failed, NO observe_error, all cached."""
    import src.grok_client as gc
    from src.utils.batch_api import BatchResult, BatchMetrics

    client = _make_client()

    submitted = [("k1", "places"), ("k2", "places")]
    ordered = [
        BatchResult(
            "srv-1", content='{"places":[{"name":"Elsenborn"}]}', finish_reason="stop"
        ),
        BatchResult(
            "srv-2", content='{"places":[{"name":"Bastogne"}]}', finish_reason="unknown"
        ),
    ]
    metrics = BatchMetrics(batch_id="b3", total_requests=2)

    with mock.patch.object(gc, "observe_error") as obs:
        failed, count_mismatch = client._map_ordered_results(
            submitted, ordered, metrics, {}, "b3"
        )

    assert count_mismatch is False
    assert failed == []
    obs.assert_not_called()
    assert client.caches["places"]["k1"] == '{"places":[{"name":"Elsenborn"}]}'
    assert client.caches["places"]["k2"] == '{"places":[{"name":"Bastogne"}]}'
    assert metrics.valid == 2
    assert metrics.empty == 0


def test_is_substantive_single_source_of_truth():
    """The 'substantive' test lives on BatchResult and treats <=2-char stripped content (and any
    non-valid result) as NOT substantive."""
    from src.utils.batch_api import BatchResult

    assert (
        BatchResult("r", content='{"a":1}', finish_reason="stop").is_substantive is True
    )
    assert BatchResult("r", content="{}", finish_reason="stop").is_substantive is False
    assert BatchResult("r", content="[]", finish_reason="stop").is_substantive is False
    assert (
        BatchResult("r", content="  {}  ", finish_reason="stop").is_substantive is False
    )
    # truncated / errored / empty are never substantive
    assert (
        BatchResult("r", content="x" * 50, finish_reason="length").is_substantive
        is False
    )
    assert BatchResult("r", content="", finish_reason="stop").is_substantive is False
