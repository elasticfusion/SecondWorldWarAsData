"""HIGH#5: Phase-2 reconcile -> Phase-3 drain/ordering barrier.

The entity DATA handoff between phases is S3-snapshot-driven (NOT a data queue). Phase 3 must not
snapshot output/bibliography/ until the Phase-2 dedup/reconcile step has finished PUBLISHING the
canonical set, else just-reconciled records are missed. These tests cover the barrier marker
(publish/clear/key) and the bounded, fail-open wait.
"""

import os
from unittest.mock import MagicMock

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402


def test_barrier_key_is_book_scoped(monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "RoerDams")
    assert ee._reconcile_barrier_key() == "manifests/reconcile_complete/RoerDams.json"
    monkeypatch.delenv("BOOK_NAME", raising=False)
    assert ee._reconcile_barrier_key() == "manifests/reconcile_complete/_all.json"


def test_publish_and_clear_barrier(monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "B")
    s3 = MagicMock()
    ee._publish_reconcile_barrier(s3)
    args = s3.put_object.call_args.kwargs
    assert args["Bucket"] == "test-bucket"
    assert args["Key"] == "manifests/reconcile_complete/B.json"
    import json

    body = json.loads(args["Body"].decode())
    assert body["book"] == "B" and body["_schema"] == "reconcile_barrier_v1"
    ee._clear_reconcile_barrier(s3)
    s3.delete_object.assert_called_once_with(
        Bucket="test-bucket", Key="manifests/reconcile_complete/B.json"
    )


def test_wait_returns_when_barrier_present(monkeypatch):
    import io
    import json

    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "30")
    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": io.BytesIO(
            json.dumps({"completed_at": ee.time.time(), "canonical_count": 7}).encode()
        )
    }
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    ee._wait_for_reconcile_barrier()
    s3.get_object.assert_called_once()
    assert ee._RECONCILE_BARRIER_STATUS == "seen"


def test_wait_fails_open_on_timeout(monkeypatch):
    # Barrier never appears; wait must time out quickly and PROCEED (fail-open), not hang.
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "1")
    s3 = MagicMock()
    s3.get_object.side_effect = Exception("404")
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee.time, "sleep", lambda n: None)
    ee._wait_for_reconcile_barrier()  # returns (does not raise/hang)
    assert s3.get_object.call_count >= 1
    assert ee._RECONCILE_BARRIER_STATUS == "timeout"


def test_wait_disabled_when_zero(monkeypatch):
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "0")
    s3 = MagicMock()
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    ee._wait_for_reconcile_barrier()
    s3.get_object.assert_not_called()
    assert ee._RECONCILE_BARRIER_STATUS == "disabled"


def test_coverage_shortfall_correlates_with_barrier_status(
    tmp_path, monkeypatch, caplog
):
    """Observability: a Phase-3 bibliography SHORTFALL is reported WITH the barrier status so the
    root cause (drain-race timeout vs a scoped-download filter) is unambiguous."""
    import logging

    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    (tmp_path / "output" / "bibliography").mkdir(parents=True)
    # local has 1 file, S3 reports 3 -> shortfall of 2.
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
    monkeypatch.setattr(ee, "_RECONCILE_BARRIER_STATUS", "timeout")
    with caplog.at_level(logging.WARNING):
        ee._observe_bibliography_download_coverage(s3)
    msg = " ".join(r.message for r in caplog.records)
    assert (
        "SHORTFALL" in msg and "TIMED OUT" in msg
    )  # cause attributed to the drain miss
