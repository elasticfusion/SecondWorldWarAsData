"""Timing statistics for every Grok batch: submit, size and kinds, first result, completion - from
whoever polls (the task, or the poller Lambda after the task is gone), written only on a change,
never breaking a batch."""

import importlib.util
from decimal import Decimal
import sys
from pathlib import Path
from unittest import mock

import boto3
import pytest
from moto import mock_aws

from src.utils import batch_stats as bs

T0 = 1794330060  # 2026-11-10 17:01:00 UTC


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("BATCH_STATS", "1")
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        t = ddb.create_table(
            TableName="t",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield t


def _row(table, bid="batch_x"):
    return table.get_item(Key={"cache_key": "batch_stats#" + bid})["Item"]


def test_a_batch_is_timed_from_submit_to_completion(table):
    bs.record_submitted(
        "batch_x",
        requests=99,
        by_type={"events": 1, "people": 20},
        name="phase2-B-21files-99reqs",
        model="grok-4.3",
        book="B",
        phase="phase2_extract",
        now=T0,
        table=table,
    )
    bs.record_progress("batch_x", 0, 99, now=T0 + 30, table=table)
    bs.record_progress("batch_x", 21, 99, 21, 0, now=T0 + 3600, table=table)
    bs.record_progress("batch_x", 99, 99, 99, 0, now=T0 + 3 * 3600, table=table)
    r = _row(table)
    assert r["submitted_hour_utc"] == 17 and r["submitted_weekday"] == "Tue"
    assert r["requests"] == 99 and r["by_type"] == {"events": 1, "people": 20}
    assert r["first_result_at"] == T0 + 3600 and r["completed_at"] == T0 + 3 * 3600
    assert r["outcome"] == "complete"
    assert [list(p) for p in r["progress"]] == [
        [T0 + 30, 0],
        [T0 + 3600, 21],
        [T0 + 3 * 3600, 99],
    ]
    s = bs.summarise(r)
    assert s["to_first_result_min"] == 60.0 and s["to_complete_min"] == 180.0


def test_an_unchanged_count_writes_nothing(table):
    assert bs.record_progress("batch_x", 21, 99, 21, 0, now=T0, table=table)
    assert not bs.record_progress("batch_x", 21, 99, 21, 0, now=T0 + 300, table=table)
    assert _row(table)["last_progress_at"] == T0


def test_the_first_result_time_is_never_moved(table):
    bs.record_progress("batch_x", 5, 99, now=T0, table=table)
    bs.record_progress("batch_x", 9, 99, now=T0 + 60, table=table)
    assert _row(table)["first_result_at"] == T0


def test_the_poller_times_a_batch_whose_task_is_gone(table):
    """No submit record (the task was interrupted): the poller's job record supplies the time."""
    bs.record_progress(
        "batch_y",
        65,
        99,
        65,
        0,
        source="poller",
        submitted_at=T0,
        now=T0 + 99,
        table=table,
    )
    r = _row(table, "batch_y")
    assert r["submitted_at"] == T0 and r["submitted_hour_utc"] == 17
    assert r["last_source"] == "poller"


def test_an_all_error_batch_is_failed(table):
    bs.record_progress("batch_x", 4, 4, 0, 4, now=T0, table=table)
    assert _row(table)["outcome"] == "failed"


def test_the_history_is_bounded_but_the_timings_are_not(table, monkeypatch):
    monkeypatch.setattr(bs, "MAX_PROGRESS_POINTS", 2)
    for i, done in enumerate((1, 2, 3, 4)):
        bs.record_progress("batch_x", done, 4, done, now=T0 + i, table=table)
    r = _row(table)
    assert len(r["progress"]) == 2
    assert r["completed_at"] == T0 + 3 and r["last_done"] == 4


def test_a_storage_failure_never_breaks_the_batch(caplog):
    broken = mock.Mock()
    broken.update_item.side_effect = RuntimeError("dynamo down")
    bs.record_submitted("b", requests=1, table=broken)
    assert bs.record_progress("b", 1, 1, table=broken) is False
    bs.record_outcome("b", "timeout", table=broken)
    assert "batch_stats.write_failed" in caplog.text


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("BATCH_STATS", "0")
    t = mock.Mock()
    bs.record_submitted("b", requests=1, table=t)
    bs.record_progress("b", 1, 1, table=t)
    t.update_item.assert_not_called()


def test_the_poll_loop_records_each_change_and_a_timeout(monkeypatch):
    from src.utils import batch_api

    states = iter([(0, 0), (0, 0), (2, 0)])

    def fake_get(*_a, **_k):
        ok, err = next(states)
        r = mock.Mock()
        r.json.return_value = {
            "state": {
                "num_requests": 2,
                "num_success": ok,
                "num_error": err,
                "num_pending": 2 - ok,
            }
        }
        return r

    monkeypatch.setattr(batch_api.requests, "get", fake_get)
    monkeypatch.setattr(batch_api.time, "sleep", lambda s: None)
    monkeypatch.setattr(batch_api, "_check_batch_submission_ok", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(
        batch_api.batch_stats, "record_progress", lambda *a, **k: calls.append(a[1])
    )
    batch_api.poll_batch("k", "batch_x", interval=0, submitted_count=2)
    assert calls == [0, 2]  # once per change, not once per poll


# --- the report ---------------------------------------------------------------------------


def _report_module():
    spec = importlib.util.spec_from_file_location(
        "batch_stats_report",
        str(Path(__file__).resolve().parents[1] / "scripts" / "batch_stats_report.py"),
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules["batch_stats_report"] = m
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


def test_batches_are_rebuilt_from_log_lines_including_the_pollers_short_ids():
    R = _report_module()
    full = "batch_0f68e82c-9170-4fb4-8b2b-7884609e9e0d"
    lines = [
        (T0, f"Batch {full}: 0/99 complete (0 success, 0 error, 99 pending)"),
        (T0 + 3600, f"Batch {full}: 21/99 complete (21 success, 0 error, 78 pending)"),
        (T0 + 7200, "Batch batch_0f68e8: 99/99 (99 ok, 0 err)"),  # the poller's prefix
        (T0, "unrelated line"),
    ]
    rows = R.rows_from_log_lines(lines)
    assert list(rows) == [full]
    assert rows[full] == {
        "submitted_at": T0,
        "first_result_at": T0 + 3600,
        "completed_at": T0 + 7200,
        "total": 99,
        "done": 99,
    }


def test_the_report_groups_by_hour_and_size():
    R = _report_module()
    rows = [
        {
            "batch_id": "a",
            "submitted_iso": "2026-11-10T03:06:00Z",
            "submitted_hour_utc": Decimal(3),
            "requests": 21,
            "submitted_at": 0,
            "first_result_at": 3600,
            "completed_at": 3660,
            "last_done": 21,
        },
        {
            "batch_id": "b",
            "submitted_iso": "2026-11-10T17:01:00Z",
            "submitted_hour_utc": 17,
            "requests": 99,
            "submitted_at": 0,
            "first_result_at": 3600,
            "last_done": 65,
        },
    ]
    text = R.report(rows)
    assert "03:00" in text and "17:00" in text
    assert "1 unfinished" in text and "10-49" in text and "50-199" in text


def test_a_batch_never_seen_to_finish_is_unknown_after_two_days():
    young = {"submitted_at": T0}
    assert bs._open_outcome(young, now=T0 + 3600) == "pending"
    assert bs._open_outcome(young, now=T0 + 3 * 86400) == "unknown"
    assert bs._open_outcome({"submitted_at": T0, "completed_at": T0 + 1}) == "complete"


def test_a_completion_after_the_task_timed_out_is_recorded_complete(table):
    bs.record_outcome("batch_x", "timeout", now=T0, table=table)
    bs.record_progress("batch_x", 4, 4, 4, 0, source="poller", now=T0 + 60, table=table)
    assert _row(table)["outcome"] == "complete"


def test_a_bad_submit_time_from_the_poller_is_only_a_lost_statistic(table):
    assert (
        bs.record_progress("batch_x", 1, 2, submitted_at="nonsense", table=table)
        is False
    )


def test_the_submit_hook_never_fails_a_submitted_batch(monkeypatch, caplog):
    import src.grok_client as gc

    client = gc.GrokClient.__new__(gc.GrokClient)
    client._batch_collector = mock.Mock(
        requests=[object()]
    )  # no cache_type: still fine
    monkeypatch.setattr(
        bs, "record_submitted", mock.Mock(side_effect=RuntimeError("boom"))
    )
    client._record_batch_submitted("batch_x", "n", 1)  # does not raise
    assert "batch_stats.write_failed" in caplog.text


def test_the_pollers_status_is_unaffected_by_a_statistics_failure(monkeypatch):
    import lambda_handlers.batch_poller as BP

    r = mock.Mock()
    r.json.return_value = {
        "state": {"num_requests": 2, "num_success": 2, "num_error": 0}
    }
    monkeypatch.setattr(BP.requests, "get", lambda *a, **k: r)
    monkeypatch.setattr(bs, "record_progress", mock.Mock(side_effect=RuntimeError("x")))
    assert BP._check_batch_status("k", "batch_x", submitted_at=T0) == "complete"
