"""Tests for job-aware NAT leases (M3, CONCURRENCY_AND_NAT_SPEC §4).

Correctness properties under test:
- Demand is present when any live lease OR running task exists (the §4 invariant).
- Expired leases (ttl in the past) do NOT count — DynamoDB TTL deletion lags, so
  we filter in-code; a crashed task cannot pin NAT up forever.
- On ANY error, the module defaults to "demand present" so NAT is never torn down
  on uncertainty.
"""

import time
from unittest.mock import MagicMock, patch

from src.utils import nat_lease


def _fake_table_with(items):
    """A fake DynamoDB Table whose scan() returns `items` (single page)."""
    table = MagicMock()
    table.scan.return_value = {"Items": items}
    return table


def test_acquire_lease_puts_item_with_ttl():
    table = MagicMock()
    with patch.object(nat_lease, "_table", return_value=table):
        assert nat_lease.acquire_lease(ttl_seconds=900, task_id="taskX") is True
    args = table.put_item.call_args.kwargs["Item"]
    assert args["cache_key"] == "nat#lease#taskX"
    assert args["ttl"] > int(time.time())


def test_acquire_lease_never_raises_on_error():
    with patch.object(nat_lease, "_table", side_effect=RuntimeError("boom")):
        assert nat_lease.acquire_lease(task_id="t") is False


def test_release_lease_deletes_item():
    table = MagicMock()
    with patch.object(nat_lease, "_table", return_value=table):
        nat_lease.release_lease(task_id="taskX")
    table.delete_item.assert_called_once_with(Key={"cache_key": "nat#lease#taskX"})


def test_live_lease_count_filters_expired():
    now = int(time.time())
    items = [
        {"cache_key": "nat#lease#a", "ttl": now + 500},  # live
        {"cache_key": "nat#lease#b", "ttl": now - 10},  # expired (TTL lag)
        {"cache_key": "nat#lease#c", "ttl": now + 900},  # live
    ]
    with patch.object(nat_lease, "_table", return_value=_fake_table_with(items)):
        assert nat_lease.live_lease_count() == 2


def test_live_lease_count_counts_missing_ttl_as_live():
    items = [{"cache_key": "nat#lease#a"}]  # no ttl -> treat as live (safe)
    with patch.object(nat_lease, "_table", return_value=_fake_table_with(items)):
        assert nat_lease.live_lease_count() == 1


def test_live_lease_count_error_assumes_demand():
    with patch.object(nat_lease, "_table", side_effect=RuntimeError("boom")):
        assert nat_lease.live_lease_count() == 1  # SAFE default


def test_has_demand_true_on_running_tasks_without_scanning():
    # running tasks > 0 short-circuits: no lease scan needed
    with patch.object(nat_lease, "live_lease_count") as lc:
        assert nat_lease.has_nat_demand(running_task_count=2) is True
        lc.assert_not_called()


def test_has_demand_true_on_pending_queue():
    with patch.object(nat_lease, "live_lease_count") as lc:
        assert nat_lease.has_nat_demand(pending_queue_depth=5) is True
        lc.assert_not_called()


def test_has_demand_from_leases_when_idle_tasks():
    with patch.object(nat_lease, "live_lease_count", return_value=1):
        assert nat_lease.has_nat_demand(running_task_count=0) is True


def test_has_demand_false_when_nothing():
    with patch.object(nat_lease, "live_lease_count", return_value=0):
        assert (
            nat_lease.has_nat_demand(running_task_count=0, pending_queue_depth=0)
            is False
        )
