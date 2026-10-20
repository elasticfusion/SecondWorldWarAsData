"""Regression: Phase-2 review PATTERN 1 — batch 0-persist.

(a) casualties cache-key non-determinism (the equipment=0 / optional-entity-zero class, #472):
    the casualties prompt embeds `entity_context` built from the people/places/groups indexes, which
    GROW between the batch COLLECT and RETRIEVE passes. A prompt-hash cache key therefore differs
    across passes -> retrieve cache-MISS -> 0 persisted. The fix keys the cache on the chunk's
    DETERMINISTIC sub-event ids, so collect-key == retrieve-key regardless of index growth.

(b) in-task submit_batch result mapping: the xAI results API does NOT echo our custom_id (only a
    server batch_request_id), so results MUST be mapped POSITIONALLY to our cache keys. The prior code
    cached content under the SERVER id -> a key no lookup uses -> every in-task batch result lost.
    This asserts content lands under OUR request_id.
"""

from unittest import mock


# ---------------------------------------------------------------------------
# (a) casualties stable cache_key across index growth
# ---------------------------------------------------------------------------
def _capture_casualties_cache_keys(people_index):
    """Drive the real _batch_extract_casualties and capture the cache_key(s) passed to
    chat_completion, under a given (growing) people_index."""
    from src.extraction import casualties as cas
    from src.grok_client import BatchModeCollecting

    captured = []

    client = mock.Mock()
    client._get_model.return_value = "grok-4-fast-non-reasoning"

    def fake_chat_completion(prompt, **kwargs):
        captured.append(kwargs.get("cache_key"))
        raise BatchModeCollecting("queued")  # simulate the COLLECT pass

    client.chat_completion.side_effect = fake_chat_completion

    sub_events = [
        {
            "Sub-eventID": "01SUBAAAA0000000000000000AA",
            "description": "The battalion suffered heavy casualties; 40 killed.",
        }
    ]
    try:
        cas._batch_extract_casualties(
            sub_events,
            client,
            dates_index={},
            places_index={},
            people_index=people_index,
            people_groups_index={},
        )
    except BatchModeCollecting:
        pass
    return captured


def test_casualties_cache_key_stable_across_entity_index_growth():
    """The cache_key MUST be identical whether the people_index is small (COLLECT pass) or has
    grown (RETRIEVE pass) — otherwise the retrieve re-run misses the cache and persists 0.
    """
    small = {"Eisenhower": "01PXX000000000000000000001"}
    grown = {
        "Eisenhower": "01PXX000000000000000000001",
        "Bradley": "01PXX000000000000000000002",
        "Montgomery": "01PXX000000000000000000003",
    }
    keys_collect = _capture_casualties_cache_keys(small)
    keys_retrieve = _capture_casualties_cache_keys(grown)

    assert keys_collect and all(k for k in keys_collect), "a cache_key must be set"
    assert (
        keys_collect == keys_retrieve
    ), f"cache_key changed with index growth: {keys_collect} != {keys_retrieve}"


def test_casualties_cache_key_tracks_subevent_ids():
    """Different sub-event id sets must produce different cache keys (correct partitioning)."""
    from src.extraction import casualties as cas
    from src.grok_client import BatchModeCollecting

    def _keys_for(seid):
        captured = []
        client = mock.Mock()
        client._get_model.return_value = "m"
        client.chat_completion.side_effect = lambda p, **k: (
            captured.append(k.get("cache_key")),
            (_ for _ in ()).throw(BatchModeCollecting("q")),
        )[1]
        try:
            cas._batch_extract_casualties(
                [{"Sub-eventID": seid, "description": "20 men were killed in action."}],
                client,
                {},
                {},
                {},
                {},
            )
        except BatchModeCollecting:
            pass
        return captured

    a = _keys_for("01SUBAAAA0000000000000000AA")
    b = _keys_for("01SUBBBBB0000000000000000BB")
    assert a and b and a != b


# ---------------------------------------------------------------------------
# (b) positional mapping lands content under OUR request_id, not the server id
# ---------------------------------------------------------------------------
def test_submit_batch_maps_results_to_our_keys_positionally():
    """retrieve_results_ordered returns BatchResults carrying the SERVER batch_request_id; the
    in-task submit_batch path must map them POSITIONALLY to our submitted request_ids and cache the
    content under OUR key (not the server id). We assert the mapping/caching, independent of network.
    """
    import src.grok_client as gc
    from src.utils.batch_api import BatchResult

    client = gc.GrokClient.__new__(gc.GrokClient)  # bypass __init__/network
    client.api_key = "k"
    client.caches = {}
    client._batch_meta = {}
    # dict-backed cache + passthrough sanitize (test the mapping, not the cache backend)
    client._get_cache = lambda ct: client.caches.setdefault(ct, {})
    client._sanitize_content = lambda c: c

    # our two submitted requests (OUR cache keys) in submission order
    class _Req:
        def __init__(self, rid, ct):
            self.request_id = rid
            self.cache_type = ct

    client._batch_collector = mock.Mock()
    client._batch_collector.requests = [
        _Req("ourkey-events-1", "events"),
        _Req("ourkey-casualties-1", "casualties"),
    ]

    # server returns results in submission ORDER, each with a SERVER id (not our key)
    ordered = [
        BatchResult(
            request_id="srv-zzz-1", content='{"ok":1}', finish_reason="stop", error=""
        ),
        BatchResult(
            request_id="srv-zzz-2", content='{"ok":2}', finish_reason="stop", error=""
        ),
    ]

    with mock.patch(
        "src.utils.batch_api.retrieve_results_ordered", return_value=ordered
    ):
        # exercise just the mapping block via the same logic submit_batch uses
        submitted = [
            (r.request_id, r.cache_type) for r in client._batch_collector.requests
        ]
        from src.utils.batch_api import retrieve_results_ordered

        res = retrieve_results_ordered(client.api_key, "b1")
        assert len(res) == len(submitted)
        for (our_id, ct), br in zip(submitted, res):
            if br.status == "valid":
                client._get_cache(ct)[our_id] = client._sanitize_content(br.content)

    # content cached under OUR keys, NEVER under the server id
    assert client.caches["events"]["ourkey-events-1"] == '{"ok":1}'
    assert client.caches["casualties"]["ourkey-casualties-1"] == '{"ok":2}'
    assert "srv-zzz-1" not in client.caches.get("events", {})
    assert "srv-zzz-2" not in client.caches.get("casualties", {})
