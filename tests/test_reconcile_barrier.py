"""HIGH#5: Phase-2 reconcile -> Phase-3 drain/ordering barrier.

The entity DATA handoff between phases is S3-snapshot-driven (NOT a data queue). Phase 3 must not
snapshot output/bibliography/ until the Phase-2 dedup/reconcile step has finished PUBLISHING the
canonical set, else just-reconciled records are missed. These tests cover the barrier marker
(publish/clear/key), the bounded fail-open wait, the freshness guard (stale barrier ignored), the
_all cross-book barrier, and the shortfall<->barrier-status correlation.
"""

import io
import json
import logging
import os
from unittest.mock import MagicMock

os.environ.setdefault("S3_BUCKET", "test-bucket")

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
    monkeypatch.setattr(ee.time, "sleep", lambda n: None)
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
