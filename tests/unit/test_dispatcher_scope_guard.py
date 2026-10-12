"""Dispatcher scope guard: the DynamoDB-queued work set is subject to the same
unoverridable known-vs-actual + ceiling rule as the local phases. Critically, an
aborted dispatch must claim NOTHING (no docs stranded as 'running').
"""

# pylint: disable=missing-function-docstring,import-error

from unittest.mock import MagicMock, patch

from lambda_handlers import dispatcher_handlers as dh
from src.utils.run_scope_guard import LARGE_RUN_CEILING


def _ready_docs(n):
    return [
        {
            "cache_key": f"doc#{i:04d}",
            "status": "held_unprocessed",
            "next_phase": "phase1",
            "book": f"B{i}",
        }
        for i in range(n)
    ]


def _table_with(items):
    t = MagicMock()
    t.scan.return_value = {"Items": items}
    t.update_item.return_value = {}
    return t


def test_undeclared_large_backlog_aborts_and_claims_nothing(monkeypatch):
    monkeypatch.delenv("DISPATCH_EXPECTED_COUNT", raising=False)
    table = _table_with(_ready_docs(LARGE_RUN_CEILING + 25))
    with patch.object(dh, "_table", return_value=table):
        out = dh.enumerate_pending({}, None)
    assert out["aborted"] is True
    assert out["count"] == 0
    assert out["items"] == []
    # CRITICAL: no doc was claimed (count-only pre-pass runs before any claim).
    table.update_item.assert_not_called()


def test_declared_large_backlog_proceeds(monkeypatch):
    # A declared large backlog is a SANCTIONED parallel multi-batch drain: the
    # guard must NOT block it (parallelism is governed by MaxConcurrency, not the
    # scope guard). All N docs enumerate + claim so the SFN Map can fan them out.
    n = LARGE_RUN_CEILING + 25
    monkeypatch.setenv("DISPATCH_EXPECTED_COUNT", str(n))
    table = _table_with(_ready_docs(n))
    with patch.object(dh, "_table", return_value=table):
        out = dh.enumerate_pending({}, None)
    assert out.get("aborted") is not True
    assert out["count"] == n
    assert table.update_item.call_count == n  # all claimed, ready for parallel Map


def test_clamp_pool_independent_of_scope_guard():
    # Concurrency sizing is orthogonal to the scope guard — clamp_pool never
    # consults DISPATCH_EXPECTED_COUNT and is unaffected by backlog size.
    out = dh.clamp_pool({"pool_min": 2, "pool_max": 8}, None)
    assert out["effective"] >= 2


def test_small_backlog_passes_undeclared(monkeypatch):
    monkeypatch.delenv("DISPATCH_EXPECTED_COUNT", raising=False)
    table = _table_with(_ready_docs(3))
    with patch.object(dh, "_table", return_value=table):
        out = dh.enumerate_pending({}, None)
    assert out["count"] == 3
    assert out.get("aborted") is not True


def test_malformed_expected_count_is_structured_abort_not_crash(monkeypatch):
    # A non-int DISPATCH_EXPECTED_COUNT must NOT crash the SFN task — it must
    # fail-loud but structured (aborted=True), claiming nothing.
    monkeypatch.setenv("DISPATCH_EXPECTED_COUNT", "not-a-number")
    table = _table_with(_ready_docs(3))
    with patch.object(dh, "_table", return_value=table):
        out = dh.enumerate_pending({}, None)
    assert out["aborted"] is True
    assert out["count"] == 0
    assert "DISPATCH_EXPECTED_COUNT" in out["reason"]
    table.update_item.assert_not_called()  # nothing claimed


def test_dispatched_set_bounded_by_guarded_snapshot(monkeypatch):
    """No TOCTOU over-dispatch: claims come ONLY from the single guarded snapshot.
    Even if some claims lose the race, the dispatched count never exceeds the
    snapshot the guard approved."""
    monkeypatch.delenv("DISPATCH_EXPECTED_COUNT", raising=False)
    n = 5  # under ceiling, undeclared OK
    table = _table_with(_ready_docs(n))
    # Simulate 2 docs already claimed by a concurrent drain (conditional fails).
    calls = {"i": 0}

    class _CCFE(Exception):
        pass

    table.meta.client.exceptions.ConditionalCheckFailedException = _CCFE

    def _update(**kwargs):
        calls["i"] += 1
        if calls["i"] <= 2:
            raise _CCFE()
        return {}

    table.update_item.side_effect = _update
    with patch.object(dh, "_table", return_value=table):
        out = dh.enumerate_pending({}, None)
    # 5 in the guarded snapshot, 2 lost the claim race -> 3 dispatched (<= 5).
    assert out["count"] == 3
    assert out["count"] <= n  # never exceeds the guarded snapshot


def test_declared_large_run_fires_approval_alert(monkeypatch):
    n = LARGE_RUN_CEILING + 5
    monkeypatch.setenv("DISPATCH_EXPECTED_COUNT", str(n))
    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    table = _table_with(_ready_docs(n))
    with (
        patch.object(dh, "_table", return_value=table),
        patch.object(dh, "_notify_large_dispatch") as alert,
    ):
        out = dh.enumerate_pending({}, None)
    assert out["count"] == n
    alert.assert_called_once_with(n)
