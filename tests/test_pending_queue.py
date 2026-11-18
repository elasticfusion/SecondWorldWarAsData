"""Task 1.2 (2026-11-18): paged prefix scans and the per-book phase queue lifecycle.

The api-cache table is 26 MB; a DynamoDB Scan returns at most 1 MB per call and `Limit` caps the
items READ before the filter, so unpaged lookups saw a few percent of the table (queued books,
NAT leases and batch jobs beyond page 1 were invisible). A fake table that pages TWO items at a
time shows the difference. Phase 3 rows: the task removes its OWN row on success, keeps and
counts it on failure (reconcile retry, owner D8c-2) and defers it at the attempt cap (1.2b).
"""

import os
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault(
    "AWS_DEFAULT_REGION", "us-east-1"
)  # the trigger module builds clients on import

from src.utils import pending_queue as pq  # noqa: E402
from src.utils.ddb_scan import scan_prefix  # noqa: E402


class FakeTable:
    """A tiny DynamoDB table: scan pages of `page` items (filter applied AFTER the page, as
    DynamoDB does), put/update/delete for the expressions pending_queue uses."""

    def __init__(self, keys=(), page=2):
        self.items = {k: {"cache_key": k} for k in keys}
        self.page = page
        self.scans = 0

    def scan(self, **kw):
        self.scans += 1
        keys = sorted(self.items)
        start = kw.get("ExclusiveStartKey", {}).get("cache_key")
        if start is not None:
            keys = [k for k in keys if k > start]
        chunk = keys[: self.page]
        prefix = kw["ExpressionAttributeValues"][":scan_prefix"]
        out = {"Items": [dict(self.items[k]) for k in chunk if k.startswith(prefix)]}
        if len(keys) > self.page:
            out["LastEvaluatedKey"] = {"cache_key": chunk[-1]}
        return out

    def update_item(
        self, Key, UpdateExpression, ExpressionAttributeValues=None, **kw
    ):  # noqa: N803
        """Only the expressions pending_queue writes (the deferral ones are checked against moto
        in tests/test_queue_deferral.py)."""
        v = ExpressionAttributeValues or {}
        cond = kw.get("ConditionExpression", "")
        current = self.items.get(Key["cache_key"])
        if "attribute_exists" in cond and (
            current is None
            or ("attempts >" in cond and current.get("attempts", 0) <= 0)
            or ("claimed_by" in cond and current.get("claimed_by") != v.get(":task"))
        ):
            err = RuntimeError("ConditionalCheckFailedException")
            err.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise err
        item = self.items.setdefault(Key["cache_key"], dict(Key))
        if UpdateExpression.startswith("REMOVE"):
            for field in UpdateExpression[len("REMOVE") :].split(","):
                item.pop(field.strip(), None)
        elif ":minus" in v:
            item["attempts"] = item.get("attempts", 0) + v[":minus"]
            if "REMOVE claimed_by" in UpdateExpression:
                item.pop("claimed_by", None)
        elif ":c" in v:
            item.update(defer_cause=v[":c"], deferred_at=v[":t"], defer_reason=v[":r"])
        else:
            item["book"] = v[":b"]
            item.setdefault("queued_at", v[":t"])
            if "ADD attempts" in UpdateExpression:
                item["attempts"] = item.get("attempts", 0) + v[":one"]
                item["started_at"] = v[":t"]
            if ":task" in v:
                item["claimed_by"] = v[":task"]
        return {"Attributes": {"attempts": item.get("attempts", 0)}}

    def delete_item(self, Key):  # noqa: N803
        self.items.pop(Key["cache_key"], None)


def _filler(n):
    return [f"answer#{i:05d}" for i in range(n)]


# --- scan_prefix -----------------------------------------------------------------------------


def test_scan_prefix_reads_every_page():
    t = FakeTable(_filler(30) + ["pending#enrich#Zebra"], page=2)
    found = list(scan_prefix(t, "pending#enrich#"))
    assert [i["cache_key"] for i in found] == ["pending#enrich#Zebra"]
    assert t.scans == 16  # 31 items / 2 per page


def test_scan_prefix_passes_projection_names_and_extra_filter():
    t = mock.MagicMock()
    t.scan.return_value = {"Items": []}
    list(
        scan_prefix(
            t,
            "batch_job#",
            projection="cache_key, #s",
            names={"#s": "status"},
            extra_filter="#s IN (:a)",
            values={":a": "pending"},
        )
    )
    kw = t.scan.call_args.kwargs
    assert (
        kw["FilterExpression"]
        == "begins_with(cache_key, :scan_prefix) AND (#s IN (:a))"
    )
    assert kw["ExpressionAttributeValues"] == {
        ":scan_prefix": "batch_job#",
        ":a": "pending",
    }
    assert kw["ProjectionExpression"] == "cache_key, #s"
    assert kw["ExpressionAttributeNames"] == {"#s": "status"}


def test_scan_prefix_refuses_an_empty_prefix():
    with pytest.raises(ValueError):
        list(scan_prefix(FakeTable(), ""))


# --- pending_queue ---------------------------------------------------------------------------


def test_a_book_beyond_the_first_page_is_found():
    t = FakeTable(_filler(40) + ["pending#enrich#Utah", "pending#parsed#Omaha"])
    assert pq.pending_books(t, "enrich") == ["Utah"]
    assert pq.next_book(t, "parsed") == "Omaha"


def test_next_book_skips_the_running_book():
    t = FakeTable(["pending#enrich#A", "pending#enrich#B"])
    assert pq.next_book(t, "enrich", exclude="A") == "B"
    assert pq.next_book(t, "enrich", exclude="B") == "A"
    assert pq.next_book(FakeTable(["pending#enrich#A"]), "enrich", exclude="A") == ""


def test_success_removes_only_the_own_row():
    t = FakeTable(["pending#enrich#A", "pending#enrich#B"])
    pq.finish(t, "enrich", "A")
    assert pq.pending_books(t, "enrich") == ["B"]


def test_each_start_is_counted_until_the_cap():
    t = FakeTable()
    assert pq.claim_attempt(t, "A", max_attempts=3) == (1, True)
    assert pq.pending_books(t, "enrich") == ["A"]  # created: a failed run is retried
    assert pq.claim_attempt(t, "A", max_attempts=3) == (2, True)
    assert pq.claim_attempt(t, "A", max_attempts=3) == (3, True)
    assert pq.claim_attempt(t, "A", max_attempts=3) == (
        4,
        False,
    )  # a 4th run is refused
    # deferred, never dropped (task 1.2b; the deferral itself: tests/test_queue_deferral.py)
    assert pq.pending_books(t, "enrich") == ["A"]


def test_requeueing_a_book_keeps_its_attempt_count():
    t = FakeTable()
    pq.claim_attempt(t, "A", max_attempts=3)
    pq.queue_enrich(t, "A")  # the trigger found the lock held again
    assert t.items["pending#enrich#A"]["attempts"] == 1


@pytest.mark.parametrize(
    "cfg,want",
    [({}, 3), ({"queue": {"phase3_max_attempts": 5}}, 5), ({"queue": {"phase3_max_attempts": 0}}, 3),
     ({"queue": {"phase3_max_attempts": "x"}}, 3), (None, 3)],
)  # fmt: skip
def test_the_attempt_cap_is_configurable(cfg, want):
    assert pq.max_attempts(cfg) == want


@pytest.mark.parametrize("kind", ["", "content", "Enrich"])
def test_an_unknown_queue_kind_is_refused(kind):
    with pytest.raises(ValueError):
        pq.pending_books(FakeTable(), kind)


@pytest.mark.parametrize("book", ["", "a#b", "nat#lease#x", " pad"])
def test_a_row_needs_a_safe_book(book):
    with pytest.raises(ValueError):
        pq.row_key("enrich", book)


# --- the ECS wrapper's use of it ---------------------------------------------------------------

import ecs_entrypoint as ee  # noqa: E402


@pytest.fixture
def table(monkeypatch):
    # per-task wrapper state must not leak into later tests (task 1.2b)
    for name in ("_stop_alerted", "_watchdog_stop", "_attempt_claimed"):
        monkeypatch.setattr(ee, name, False)
    t = FakeTable(_filler(20))
    monkeypatch.setattr(ee, "_cache_table", lambda: t)
    monkeypatch.setattr(ee, "_current_phase_script", "phase3_enrich_data.py")
    monkeypatch.setenv("BOOK_NAME", "Utah")
    return t


def test_the_running_book_is_never_its_own_next_book(table):
    table.items["pending#enrich#Utah"] = {"cache_key": "pending#enrich#Utah"}
    assert ee._get_next_pending_enrich() == ""
    table.items["pending#enrich#Omaha"] = {"cache_key": "pending#enrich#Omaha"}
    assert ee._get_next_pending_enrich() == "Omaha"


def test_success_removes_the_own_row_and_leaves_the_next(table):
    for b in ("Utah", "Omaha"):
        table.items[f"pending#enrich#{b}"] = {"cache_key": f"pending#enrich#{b}"}
    ee._finish_own_enrich_row()
    assert pq.pending_books(table, "enrich") == ["Omaha"]


def test_each_phase3_start_is_counted_and_the_cap_stops_a_retry_loop(
    table, monkeypatch
):
    errors = []
    monkeypatch.setattr(
        ee, "observe_error", lambda scope, event, **k: errors.append(event) or "error"
    )
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    assert [ee._claim_enrich_attempt() for _ in range(3)] == [True, True, True]
    assert pq.pending_books(table, "enrich") == ["Utah"] and errors == []
    assert ee._claim_enrich_attempt() is False
    assert pq.pending_books(table, "enrich") == ["Utah"] and errors == [
        "enrich_deferred"
    ]


def test_a_failed_run_leaves_the_book_queued_and_success_removes_it(table, monkeypatch):
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    assert ee._claim_enrich_attempt() is True
    # whatever ends this run (crash, preflight abort, SIGTERM): the row is still there
    assert pq.pending_books(table, "enrich") == ["Utah"]
    ee._finish_own_enrich_row()
    assert pq.pending_books(table, "enrich") == []


def test_a_sigterm_after_success_does_not_requeue_the_book(table, monkeypatch):
    """SIGTERM writes nothing to the queue: a book that just succeeded stays done."""
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    ee._claim_enrich_attempt()
    ee._finish_own_enrich_row()
    for name in ("_final_sync", "_remove_lock", "_release_nat_lease"):
        monkeypatch.setattr(ee, name, lambda *a, **k: None)
    monkeypatch.setattr(ee, "_requeue_interrupted_resume", lambda: None)
    monkeypatch.setattr(ee, "observe_error", lambda *a, **k: "error")
    monkeypatch.setattr(ee, "_stop_alerted", False)
    with pytest.raises(SystemExit):
        ee._handle_sigterm(15, None)
    assert pq.pending_books(table, "enrich") == []


def test_other_phases_are_not_counted_here(table, monkeypatch):
    monkeypatch.setattr(ee, "_current_phase_script", "phase2_extract.py")
    assert ee._claim_enrich_attempt() is True
    assert pq.pending_books(table, "enrich") == []


def test_a_claim_failure_warns_and_runs(table, monkeypatch):
    warns = []
    monkeypatch.setattr(ee, "observe_warn", lambda s, e, **k: warns.append(e))
    monkeypatch.setattr(
        table, "update_item", mock.MagicMock(side_effect=RuntimeError("x"))
    )
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    assert ee._claim_enrich_attempt() is True
    assert warns == ["claim_failed"]


def test_run_phase_counts_first_and_stops_past_the_cap(monkeypatch):
    monkeypatch.setattr(ee, "_claim_enrich_attempt", lambda: False)
    removed = []
    monkeypatch.setattr(ee, "_remove_lock", lambda s: removed.append(s))
    monkeypatch.setattr(
        ee, "_acquire_nat_lease", mock.MagicMock(side_effect=AssertionError("ran"))
    )
    with pytest.raises(SystemExit) as stop:
        ee.run_phase("phase3_enrich_data.py", [])
    assert stop.value.code == 0 and removed == ["phase3_enrich_data.py"]


def test_the_wrapper_no_longer_deletes_the_next_books_row():
    src = ee.Path(ee.__file__).read_text(encoding="utf-8")
    assert "_consume_pending_enrich" not in src


# --- the trigger Lambda and NAT demand checks --------------------------------------------------


def test_the_trigger_reads_every_page(monkeypatch):
    import lambda_handlers.trigger_handler as th

    t = FakeTable(_filler(30) + ["pending#enrich#Utah", "pending#parsed#Omaha"])
    monkeypatch.setattr(th, "dynamo", t)
    assert th._get_pending_books_for_enrich() == ["Utah"]
    assert th._get_pending_books() == ["Omaha"]
    th._queue_pending_enrich("Brest")
    assert th._get_pending_books_for_enrich() == ["Brest", "Utah"]


def test_no_unpaged_prefix_scan_remains_in_the_queue_and_nat_code():
    from pathlib import Path

    root = Path(ee.__file__).parent
    for rel in (
        "ecs_entrypoint.py",
        "lambda_handlers/trigger_handler.py",
        "lambda_handlers/nat_manager.py",
        "lambda_handlers/nat_teardown.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        for marker in (
            '"pending#enrich#"',
            '"pending#parsed#"',
            '"nat#lease#"',
            '"lock#"',
            '"batch_job#"',
        ):
            for line in text.splitlines():
                if marker in line and "ExpressionAttributeValues" in line:
                    pytest.fail(f"{rel}: hand-rolled scan for {marker}: {line.strip()}")
        # these four files had the capped scans; none may come back
        assert "Limit=100" not in text and "Limit=1000" not in text, rel


def test_a_nat_lease_beyond_the_first_page_keeps_nat(monkeypatch):
    import time

    import lambda_handlers.nat_manager as nm

    t = FakeTable(_filler(30))
    t.items["nat#lease#task-9"] = {
        "cache_key": "nat#lease#task-9",
        "ttl": int(time.time()) + 600,
    }
    monkeypatch.setattr(nm, "_lease_table", lambda: t)
    assert nm._nat_live_lease_present(int(time.time())) is True


def test_the_reconcile_launches_the_first_book_whose_lock_is_free(monkeypatch):
    import lambda_handlers.trigger_handler as th

    monkeypatch.setattr(th, "_get_pending_books_for_enrich", lambda: ["A", "B", "C"])
    tried = []

    def _run(task_def, source, book_name=""):
        tried.append(book_name)
        return book_name == "B"  # A is running (lock held), B launches

    monkeypatch.setattr(th, "_run_task", _run)
    assert th._reconcile_phase3("hourly") is True
    assert tried == ["A", "B"]


def test_scan_prefix_refuses_a_value_named_like_the_prefix():
    with pytest.raises(ValueError):
        list(scan_prefix(FakeTable(), "lock#", values={":scan_prefix": "x"}))


def test_the_nat_teardown_signal_reads_every_page(monkeypatch):
    import time

    import lambda_handlers.nat_teardown as nt

    t = FakeTable(_filler(30))
    t.items["nat#lease#task-9"] = {
        "cache_key": "nat#lease#task-9",
        "ttl": int(time.time()) + 600,
    }
    res = mock.MagicMock()
    res.Table.return_value = t
    monkeypatch.setattr("boto3.resource", lambda *a, **k: res)
    assert nt._ddb_networking_signal() is True
    del t.items["nat#lease#task-9"]
    assert nt._ddb_networking_signal() is False


def test_the_nat_teardown_signal_fails_safe(monkeypatch):
    import lambda_handlers.nat_teardown as nt

    monkeypatch.setattr("boto3.resource", mock.MagicMock(side_effect=RuntimeError("x")))
    assert nt._ddb_networking_signal() is True


def test_the_grok_batch_demand_check_passes_its_status_filter(monkeypatch):
    import lambda_handlers.nat_manager as nm

    t = mock.MagicMock()
    t.scan.side_effect = [
        {"Items": [], "LastEvaluatedKey": {"cache_key": "x"}},
        {
            "Items": [
                {
                    "cache_key": "batch_job#1",
                    "status": "pending",
                    "submitted_at": 10**10,
                }
            ]
        },
    ]
    monkeypatch.setattr("src.utils.job_queue._get_table", lambda: t)
    assert nm._grok_batch_pending() is True
    kw = t.scan.call_args_list[0].kwargs
    assert kw["FilterExpression"].endswith("AND (#s IN (:pending, :ready))")
    assert kw["ExpressionAttributeNames"] == {"#s": "status"}
    assert t.scan.call_count == 2
