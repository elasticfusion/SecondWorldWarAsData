"""Task 1.3 (owner decision D10): the Phase 3 lock is released on failure, at once, and on
success only after the post-sync repair and the provenance gate, before the next book starts.
"""

import os
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402

PHASE3 = "phase3_enrich_data.py"


def _stub_run_phase_to_failure(monkeypatch, order):
    """Everything in run_phase up to the phase subprocess, which exits non-zero."""
    monkeypatch.setattr(ee, "_claim_enrich_attempt", lambda: True)
    for name in (
        "_acquire_nat_lease",
        "_ensure_nat_if_needed",
        "_clear_title_cache_if_reprocess",
        "_load_secrets",
        "_preflight_credit_check",
        "_preflight_notification_subscriptions",
        "_preflight_runtime_tables",
        "_patch_config",
        "_start_openserp_if_needed",
        "_setup_symlinks",
        "_download_inputs",
        "_stamp_schema_versions",
        "_audit_failed_phase2",
    ):
        monkeypatch.setattr(ee, name, lambda *a, **k: None)
    monkeypatch.setattr(ee, "WORKDIR", mock.MagicMock())
    monkeypatch.setattr(ee, "BackgroundSync", mock.MagicMock())
    monkeypatch.setattr(ee, "_should_use_batch_mode", lambda s: False)
    monkeypatch.setattr(ee, "_resolve_ecs_batch_args", lambda s, a, n, y: (False, a))
    monkeypatch.setattr(ee.subprocess, "run", lambda *a, **k: mock.Mock(returncode=1))
    for name, tag in (
        ("_notify_failure", "alert"),
        ("_final_sync_failed_phase", "sync"),
        ("_remove_lock", "lock"),
        ("_release_nat_lease", "nat"),
    ):
        monkeypatch.setattr(ee, name, lambda *a, _t=tag, **k: order.append(_t))


@pytest.mark.parametrize("script", [PHASE3, "phase2_extract.py"])
def test_a_failed_phase_releases_its_lock_at_once(monkeypatch, script):
    order = []
    _stub_run_phase_to_failure(monkeypatch, order)
    with pytest.raises(SystemExit) as stop:
        ee.run_phase(script, [])
    assert stop.value.code == 1
    # alerted and synced first, then the lock goes, before the task exits
    assert order == ["alert", "sync", "lock", "nat"]


def _stub_phase3_post_process(monkeypatch, order, fix=None):
    monkeypatch.setattr(
        ee,
        "_phase3_durable_referential_fix",
        fix or (lambda: order.append("repair") or True),
    )
    monkeypatch.setattr(
        ee,
        "_provenance_gate",
        lambda p, refresh=True: order.append("gate") or (True, ""),
    )
    monkeypatch.setattr(ee, "_phase_end_held", lambda r, p: False)
    monkeypatch.setattr(ee, "_advance_doc_lifecycle", lambda p: order.append("done"))
    monkeypatch.setattr(ee, "_finish_own_enrich_row", lambda: order.append("finish"))
    monkeypatch.setattr(ee, "_remove_lock", lambda s: order.append("lock"))
    monkeypatch.setattr(
        ee, "_get_next_pending_enrich", lambda: order.append("next") or ""
    )
    for name in (
        "_notify_url_review_pending",
        "_stop_openserp_if_running",
        "_notify_complete",
        "publish_alert",
    ):
        monkeypatch.setattr(ee, name, mock.Mock())
    monkeypatch.setattr(ee, "_get_next_pending_book", lambda: "")


def test_phase3_releases_the_lock_after_the_repair_and_before_the_next_book(
    monkeypatch,
):
    order = []
    _stub_phase3_post_process(monkeypatch, order)
    ee._post_process(PHASE3, {})
    # (a second "next" is the end-of-task check on whether networking can come down)
    assert order[:6] == ["repair", "gate", "done", "finish", "lock", "next"]
    assert order.count("lock") == 1


def test_a_repair_crash_still_releases_the_lock_and_keeps_the_book_queued(
    monkeypatch,
):
    order = []

    def boom():
        raise RuntimeError("s3 down")

    _stub_phase3_post_process(monkeypatch, order, fix=boom)
    with pytest.raises(RuntimeError):
        ee._post_process(PHASE3, {})
    # lock freed for the reconcile's retry; the row is not finished, so the book stays queued;
    # no next book is started from a crashed task (the _dispatch crash handler alerts)
    assert order == ["lock"]


def test_a_failure_step_that_raises_still_releases_the_lock(monkeypatch):
    order = []
    _stub_run_phase_to_failure(monkeypatch, order)

    def boom(*a, **k):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(ee, "_final_sync_failed_phase", boom)
    with pytest.raises(RuntimeError):
        ee.run_phase(PHASE3, [])
    assert order == ["alert", "lock", "nat"]


def test_the_lock_is_deleted_once_per_task(monkeypatch):
    """A second release (SIGTERM during the repair, then its finally) must not delete the lock
    a retry task may have taken in between: the delete has no owner check."""
    table = mock.MagicMock()
    monkeypatch.setattr(ee, "_released_locks", set())
    monkeypatch.setattr(ee, "_lock_key", lambda s: "lock#phase3#Utah")
    monkeypatch.setattr(
        ee.boto3, "resource", lambda *a, **k: mock.Mock(Table=lambda n: table)
    )
    ee._remove_lock(PHASE3)
    ee._remove_lock(PHASE3)
    table.delete_item.assert_called_once_with(Key={"cache_key": "lock#phase3#Utah"})


def test_a_failed_delete_can_be_retried_and_says_how_to_fix_it(monkeypatch, caplog):
    table = mock.MagicMock()
    table.delete_item.side_effect = [RuntimeError("throttled"), None]
    monkeypatch.setattr(ee, "_released_locks", set())
    monkeypatch.setattr(ee, "_lock_key", lambda s: "lock#phase3#Utah")
    monkeypatch.setattr(
        ee.boto3, "resource", lambda *a, **k: mock.Mock(Table=lambda n: table)
    )
    ee._remove_lock(PHASE3)
    assert "delete item cache_key=lock#phase3#Utah" in caplog.text
    ee._remove_lock(PHASE3)
    assert table.delete_item.call_count == 2
