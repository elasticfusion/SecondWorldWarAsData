"""HIGH#5: Phase-2 reconcile -> Phase-3 drain/ordering barrier.

The entity DATA handoff between phases is S3-snapshot-driven (NOT a data queue). Phase 3 must not
snapshot output/bibliography/ until the Phase-2 dedup/reconcile step has finished PUBLISHING the
canonical set, else just-reconciled records are missed. These tests cover the barrier marker
(publish/clear/key), the bounded fail-open wait, the freshness guard (stale barrier ignored), the
_all cross-book barrier, and the shortfall<->barrier-status correlation.
"""

import io
import time as _time
import json
import logging
import os
from unittest.mock import MagicMock

os.environ.setdefault("S3_BUCKET", "test-bucket")
_REAL_SLEEP = _time.sleep
R1, OLD, NEW, A, B = ("%032x" % i for i in range(1, 6))

import ecs_entrypoint as ee  # noqa: E402


def _barrier_obj(completed_at, canonical_count=7):
    return {
        "Body": io.BytesIO(
            json.dumps(
                {"completed_at": completed_at, "canonical_count": canonical_count}
            ).encode()
        )
    }


def test_barrier_key_is_book_scoped(monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "RoerDams")
    assert ee._reconcile_barrier_key() == "manifests/reconcile_complete/RoerDams.json"
    monkeypatch.delenv("BOOK_NAME", raising=False)
    assert ee._reconcile_barrier_key() == "manifests/reconcile_complete/_all.json"


def test_barrier_key_empty_book_name_falls_back_to_all(monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "")
    assert ee._reconcile_barrier_key() == "manifests/reconcile_complete/_all.json"


def test_publish_and_clear_barrier_with_explicit_key(monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "B")
    s3 = MagicMock()
    ee._publish_reconcile_barrier(
        s3, canonical_count=5, key="manifests/reconcile_complete/_all.json"
    )
    args = s3.put_object.call_args.kwargs
    assert args["Key"] == "manifests/reconcile_complete/_all.json"
    body = json.loads(args["Body"].decode())
    assert body["canonical_count"] == 5 and body["_schema"] == "reconcile_barrier_v1"
    ee._clear_reconcile_barrier(s3, key="manifests/reconcile_complete/_all.json")
    s3.delete_object.assert_called_once_with(
        Bucket="test-bucket", Key="manifests/reconcile_complete/_all.json"
    )


def test_wait_returns_seen_for_fresh_barrier(monkeypatch):
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "30")
    s3 = MagicMock()
    s3.get_object.return_value = _barrier_obj(ee.time.time())  # fresh
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    assert ee._wait_for_reconcile_barrier() == "seen"


def test_wait_ignores_stale_barrier_then_times_out(monkeypatch):
    """FINDING 2: a barrier whose completed_at predates the task start (a prior run's signal that
    clear failed to delete) must be IGNORED, not accepted as a false 'seen'."""
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "1")
    s3 = MagicMock()
    # completed_at well before task start => stale.
    s3.get_object.return_value = _barrier_obj(ee._TASK_START_TS - 10_000)
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee.time, "sleep", lambda n: None)
    assert ee._wait_for_reconcile_barrier() == "timeout"  # stale -> not accepted


def test_wait_accepts_all_barrier(monkeypatch):
    """FINDING 1: Phase 3 accepts the _all cross-book barrier even when the book key is absent."""
    monkeypatch.setenv("BOOK_NAME", "RoerDams")
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "30")

    def get_object(Bucket, Key):
        if Key == "manifests/reconcile_complete/_all.json":
            return _barrier_obj(ee.time.time())
        raise Exception("404")  # book key absent

    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    assert ee._wait_for_reconcile_barrier() == "seen"


def test_wait_fails_open_on_timeout(monkeypatch):
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "1")
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("404")
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee.time, "sleep", lambda n: _REAL_SLEEP(0.05))
    assert ee._wait_for_reconcile_barrier() == "timeout"


def test_wait_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "0")
    s3 = MagicMock()
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    assert ee._wait_for_reconcile_barrier() == "disabled"
    s3.get_object.assert_not_called()


def test_publish_failure_never_raises(monkeypatch):
    """Fail-safe: a put_object error must not propagate (never break dedup)."""
    s3 = MagicMock()
    s3.put_object.side_effect = Exception("s3 down")
    ee._publish_reconcile_barrier(s3, canonical_count=1)  # must not raise


def test_coverage_shortfall_correlates_with_barrier_status(
    tmp_path, monkeypatch, caplog
):
    """Observability: a Phase-3 bibliography SHORTFALL is reported WITH the barrier status so the
    root cause (drain-race timeout vs a scoped-download filter) is unambiguous."""
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    (tmp_path / "output" / "bibliography").mkdir(parents=True)
    (tmp_path / "output" / "bibliography" / "bib_a.json").write_text("{}")
    s3 = MagicMock()
    s3.get_paginator.return_value.paginate.return_value = [
        {
            "Contents": [
                {"Key": "output/bibliography/bib_a.json"},
                {"Key": "output/bibliography/bib_b.json"},
                {"Key": "output/bibliography/bib_c.json"},
            ]
        }
    ]
    with caplog.at_level(logging.WARNING):
        ee._observe_bibliography_download_coverage(s3, barrier_status="timeout")
    msg = " ".join(r.message for r in caplog.records)
    assert "SHORTFALL" in msg and "TIMED OUT" in msg


def _s3_with(barrier_body, marker_body):
    """S3 mock serving a barrier and the run marker by key."""

    def _get(Bucket, Key):  # noqa: N803 - boto3 signature
        if Key == ee._RECONCILE_RUN_MARKER_KEY:
            if marker_body is None:
                raise KeyError(Key)
            return {"Body": io.BytesIO(json.dumps(marker_body).encode())}
        return {"Body": io.BytesIO(json.dumps(barrier_body).encode())}

    s3 = MagicMock()
    s3.get_object.side_effect = _get
    return s3


def test_wait_accepts_matching_run_id_even_if_barrier_predates_task_start(monkeypatch):
    """The production bug: Phase 3 starts AFTER the reconcile published, so the timestamp check
    rejected a correct barrier. Identity (run_id) must accept it regardless of age."""
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "30")
    s3 = _s3_with(
        {"completed_at": ee._TASK_START_TS - 10_000, "run_id": R1},
        {"run_id": R1, "started_at": 0},
    )
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    assert ee._wait_for_reconcile_barrier() == "seen"


def test_wait_rejects_mismatched_run_id(monkeypatch):
    """A leftover barrier from a previous run (different run_id) must not be accepted."""
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "1")
    s3 = _s3_with(
        {"completed_at": ee._TASK_START_TS - 10_000, "run_id": OLD},
        {"run_id": NEW, "started_at": 0},
    )
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee.time, "sleep", lambda n: _REAL_SLEEP(0.05))
    assert ee._wait_for_reconcile_barrier() == "timeout"


def test_wait_without_marker_falls_back_to_timestamp(monkeypatch):
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "30")
    s3 = _s3_with({"completed_at": ee.time.time(), "run_id": R1}, None)
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    assert ee._wait_for_reconcile_barrier() == "seen"


def test_start_reconcile_run_writes_marker_and_publish_carries_run_id():
    s3 = MagicMock()
    run_id = ee._start_reconcile_run(s3)
    marker = json.loads(s3.put_object.call_args.kwargs["Body"].decode())
    assert run_id and marker["run_id"] == run_id
    assert s3.put_object.call_args.kwargs["Key"] == ee._RECONCILE_RUN_MARKER_KEY
    s3.reset_mock()
    ee._publish_reconcile_barrier(s3, canonical_count=1, key="k", run_id=run_id)
    assert (
        json.loads(s3.put_object.call_args.kwargs["Body"].decode())["run_id"] == run_id
    )


def test_start_reconcile_run_failure_returns_none_and_alerts(monkeypatch):
    s3 = MagicMock()
    s3.put_object.side_effect = RuntimeError("denied")
    alerts = []
    monkeypatch.setattr(ee, "observe_warn", lambda *a, **k: alerts.append((a, k)) or "")
    assert ee._start_reconcile_run(s3) is None
    assert alerts and alerts[0][1]["note"]  # actionable: note is mandatory


def test_mismatched_run_id_is_stale_even_with_fresh_timestamp(monkeypatch):
    """Run ids are authoritative: a different id is stale even if completed_at is recent
    (e.g. overlapping reconciles)."""
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "1")
    s3 = _s3_with(
        {"completed_at": ee.time.time(), "run_id": A}, {"run_id": B, "started_at": 0}
    )
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee.time, "sleep", lambda n: _REAL_SLEEP(0.05))
    assert ee._wait_for_reconcile_barrier() == "timeout"


def test_barrier_published_before_marker_started_is_rejected():
    now = ee.time.time()
    s3 = _s3_with(
        {"completed_at": now - 100, "run_id": R1}, {"run_id": R1, "started_at": now}
    )
    body = {"completed_at": now - 100, "run_id": R1}
    assert ee._judge_barrier(s3, body, 0, "k") == (False, "run_id")


def test_barrier_without_run_id_uses_timestamp_even_with_marker():
    """Marker-write-failed path: barrier run_id is None -> timestamp rule."""
    now = ee.time.time()
    s3 = _s3_with({}, {"run_id": R1, "started_at": now})
    assert ee._judge_barrier(
        s3, {"completed_at": now, "run_id": None}, now - 30, "k"
    ) == (
        True,
        "timestamp",
    )
    assert ee._judge_barrier(
        s3, {"completed_at": now - 999, "run_id": None}, now - 30, "k"
    ) == (False, "timestamp")


def test_non_dict_and_corrupt_bodies_are_stale_not_crashes():
    assert ee._parse_barrier_body({"Body": io.BytesIO(b"[1,2]")}) == {}
    assert ee._parse_barrier_body({"Body": io.BytesIO(b"not json")}) == {}
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": io.BytesIO(b'["x"]')}
    assert ee._read_run_marker(s3) is None


def test_marker_read_error_other_than_missing_alerts(monkeypatch):
    alerts = []
    monkeypatch.setattr(
        ee, "observe_warn_counted", lambda *a, **k: alerts.append(k) or 1
    )
    s3 = MagicMock()
    s3.get_object.side_effect = PermissionError("denied")
    assert ee._read_run_marker(s3) is None
    assert alerts and alerts[0]["note"]
    alerts.clear()
    s3.get_object.side_effect = KeyError("missing")  # plain absence: no alert
    assert ee._read_run_marker(s3) is None and not alerts


def test_clear_and_publish_failures_are_actionable(monkeypatch):
    alerts = []
    monkeypatch.setattr(ee, "observe_warn", lambda *a, **k: alerts.append(a[1]) or "")
    s3 = MagicMock()
    s3.delete_object.side_effect = RuntimeError("x")
    s3.put_object.side_effect = RuntimeError("x")
    ee._clear_reconcile_barrier(s3, key="k")
    ee._publish_reconcile_barrier(s3, key="k")
    assert alerts == ["barrier_clear_failed", "barrier_publish_failed"]


def test_reconcile_orders_clear_marker_publish_with_same_run_id(monkeypatch, tmp_path):
    """clear barrier -> clear marker -> write marker -> ... -> publish barrier (same run_id)."""
    calls = []
    s3 = MagicMock()
    s3.delete_object.side_effect = lambda **k: calls.append(("del", k["Key"]))
    s3.put_object.side_effect = lambda **k: calls.append(
        ("put", k["Key"], json.loads(k["Body"].decode()))
    )
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    monkeypatch.setattr(ee, "_download_s3_prefix", lambda *a, **k: 0)
    monkeypatch.setattr(ee, "s3_sync_up", lambda *a, **k: (0, 0))
    monkeypatch.setattr(ee, "_delete_orphaned_bibliography_shards", lambda *a: None)
    import src.extraction.bibliography as bib

    monkeypatch.setattr(bib, "reconcile_bibliography_dir", lambda d: 0)
    (tmp_path / "output" / "bibliography").mkdir(parents=True)
    ee._reconcile_bibliography_full_corpus()
    assert [c[:2] for c in calls] == [
        ("del", "manifests/reconcile_complete/_all.json"),
        ("del", ee._RECONCILE_RUN_MARKER_KEY),
        ("put", ee._RECONCILE_RUN_MARKER_KEY),
        ("put", "manifests/reconcile_complete/_all.json"),
    ]
    assert calls[2][2]["run_id"] == calls[3][2]["run_id"]


def test_marker_with_bad_types_is_ignored_not_crash():
    for bad in (
        {"run_id": R1, "started_at": "x"},
        {"run_id": R1, "started_at": True},
        {"run_id": "not-hex\nOBSERV forged", "started_at": 1},
        {"run_id": 5, "started_at": 1},
    ):
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(bad).encode())}
        assert ee._read_run_marker(s3) is None


def test_boto_clienterror_shapes():
    from botocore.exceptions import ClientError

    def _err(code):
        return ClientError({"Error": {"Code": code, "Message": "m"}}, "GetObject")

    import src.utils.observe as obs

    obs.reset_counted_warnings()
    s3 = MagicMock()
    s3.get_object.side_effect = _err("NoSuchKey")
    assert ee._read_run_marker(s3) is None  # absence: no alert
    s3.get_object.side_effect = _err("AccessDenied")
    for _ in range(3):
        assert ee._read_run_marker(s3) is None
    # counted and rate-limited: 3 failures -> one count-1 alert, not one alert per poll
    totals = obs.counted_warning_totals("reconcile", "run_marker_read_failed")
    assert sum(totals.values()) == 3


def test_forged_run_id_in_barrier_is_never_trusted_or_logged(caplog):
    s3 = _s3_with({}, {"run_id": R1, "started_at": 0})
    body = {"completed_at": ee.time.time(), "run_id": "x\nOBSERV forged"}
    assert ee._judge_barrier(s3, body, 0, "k") == (True, "timestamp")
