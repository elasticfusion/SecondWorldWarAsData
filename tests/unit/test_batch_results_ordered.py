"""xAI batch results don't echo custom_id -> map by submission order (retrieve_results_ordered)."""

import os

os.environ.setdefault("S3_BUCKET", "test-bucket")

from unittest.mock import patch, MagicMock

from src.utils import batch_api


def _page(items, token=None):
    return {"results": items, "pagination_token": token}


def _item(content, rid="server-rid"):
    return {
        "batch_request_id": rid,
        "batch_result": {
            "response": {
                "chat_get_completion": {
                    "choices": [
                        {"message": {"content": content}, "finish_reason": "stop"}
                    ]
                }
            }
        },
    }


def test_ordered_preserves_submission_order_across_pages():
    pages = [
        _page([_item("A", "s1"), _item("B", "s2")], token="t1"),
        _page([_item("C", "s3")], token=None),
    ]
    resp = MagicMock()
    resp.json.side_effect = pages
    resp.raise_for_status = lambda: None
    with patch("src.utils.batch_api.requests.get", return_value=resp):
        ordered = batch_api.retrieve_results_ordered("k", "batch_x")
    assert [r.content for r in ordered] == ["A", "B", "C"]  # order preserved
    # server rids are opaque; mapping to our keys is by POSITION, done by the caller.
    assert all(r.finish_reason == "stop" for r in ordered)


class _BR:
    """Thin wrapper over the real BatchResult so .ok uses production content-first semantics."""

    def __new__(cls, content="", finish_reason="stop"):
        return batch_api.BatchResult(
            request_id="r", content=content, finish_reason=finish_reason
        )


def test_map_count_match_populates_under_our_keys():
    import ecs_entrypoint as e

    submitted = [("key_a", "events"), ("key_b", "supplemental")]
    ordered = [_BR('{"x":1}'), _BR('{"y":2}')]
    out = e._map_batch_results_by_index(submitted, ordered, "b1")
    assert out == {"events": {"key_a": '{"x":1}'}, "supplemental": {"key_b": '{"y":2}'}}


def test_map_caches_content_bearing_unknown_finish_reason():
    """Re-review HIGH: retrieve_results_ordered defaults finish_reason to 'unknown' when the API
    omits it. The ECS retrieve-only mapping must cache such content (content-first br.ok) — it
    previously hardcoded finish_reason=='stop' and silently dropped it (0-record async bug).
    """
    import ecs_entrypoint as e

    submitted = [("key_a", "events"), ("key_b", "supplemental")]
    ordered = [
        _BR('{"x":1}', finish_reason="unknown"),
        _BR('{"y":2}', finish_reason=""),
    ]
    out = e._map_batch_results_by_index(submitted, ordered, "b1")
    assert out == {"events": {"key_a": '{"x":1}'}, "supplemental": {"key_b": '{"y":2}'}}


def test_map_skips_truncated_and_errored():
    """A 'length' truncation (still routed to halving) and an empty/errored entry are NOT cached,
    without shifting the positional index."""
    import ecs_entrypoint as e

    submitted = [("k1", "events"), ("k2", "events"), ("k3", "events")]
    ordered = [
        _BR('{"ok":1}', finish_reason="length"),  # truncated -> skip
        batch_api.BatchResult(
            "r", content="", finish_reason="unknown"
        ),  # empty -> skip
        _BR(
            '{"ok":3}', finish_reason="unknown"
        ),  # valid -> cached under k3 (no index shift)
    ]
    out = e._map_batch_results_by_index(submitted, ordered, "b1")
    assert out == {"events": {"k3": '{"ok":3}'}}


def test_map_count_mismatch_populates_nothing():
    import ecs_entrypoint as e

    out = e._map_batch_results_by_index([("k", "events")], [_BR("a"), _BR("b")], "b1")
    assert out == {}  # fail-safe, no mis-key


def test_map_errored_entry_skipped_without_index_shift():
    import ecs_entrypoint as e

    submitted = [("k1", "events"), ("k2", "supplemental"), ("k3", "events")]
    ordered = [_BR("A"), _BR("", finish_reason="error"), _BR("C")]
    out = e._map_batch_results_by_index(submitted, ordered, "b1")
    # k2 skipped (errored); k1->A, k3->C stay correctly positioned
    assert out["events"] == {"k1": "A", "k3": "C"}
    assert "supplemental" not in out


def test_map_strips_control_chars():
    import ecs_entrypoint as e

    out = e._map_batch_results_by_index([("k", "events")], [_BR("a\x00b\x07c")], "b1")
    assert out["events"]["k"] == "abc"
