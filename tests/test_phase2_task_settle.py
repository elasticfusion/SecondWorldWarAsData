"""After a Phase 2 run the ECS task settles from its exit status (ecs_entrypoint._settle_phase2_task):
75 = a batch round was handed off -> enqueue THAT batch for the poller (never "the newest metrics
file": a resumed task also holds earlier rounds' metrics) and end; 0 = the book is finished ->
post-process; anything else (incl. the round cap) -> failed, nothing enqueued."""

import json
from unittest import mock

import pytest


@pytest.fixture
def ep(tmp_path, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import ecs_entrypoint

    monkeypatch.setattr(ecs_entrypoint, "WORKDIR", tmp_path)
    (tmp_path / "output" / "metrics").mkdir(parents=True)
    for name in (
        "_final_sync",
        "_final_sync_failed_phase",
        "_stop_openserp_if_running",
        "_post_process",
        "_audit_failed_phase2",
    ):
        monkeypatch.setattr(ecs_entrypoint, name, mock.Mock())
    return ecs_entrypoint


def _results(ep, **body):
    (ep.WORKDIR / "output" / ".phase_results.json").write_text(json.dumps(body))


def _metrics(ep, batch_id, mtime, answered=0):
    import os

    f = ep.WORKDIR / "output" / "metrics" / f"batch_{batch_id}_{mtime}.json"
    f.write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "batch_name": f"phase2-B-1files-r2-{batch_id}",
                "total_requests": 10,
                "api_successes": answered,
            }
        )
    )
    os.utime(f, (mtime, mtime))


def test_a_handed_off_round_enqueues_that_batch_and_ends(ep):
    _results(ep, pending_batch="new", pending_batch_name="phase2-B-1files-r2-new")
    _metrics(ep, "new", 1000)
    _metrics(ep, "older", 2000)  # newer file, earlier round (downloaded from S3)
    with (
        mock.patch.object(ep, "_enqueue_or_alert") as enqueue,
        mock.patch.object(ep, "_notify_batch_submitted") as old_notice,
        mock.patch.object(ep, "_alert_handed_off") as alert,
        mock.patch("src.utils.job_queue.update_job_status") as status,
        mock.patch("src.utils.job_queue.get_active_jobs", return_value=[]),
        mock.patch("src.utils.job_queue._get_table"),
    ):
        ep._settle_phase2_task("phase2_extract.py", 75, retrieved_batch="prev")
    assert enqueue.call_args.args[0] == "new"
    assert enqueue.call_args.kwargs["status"] == "pending"
    assert enqueue.call_args.args[3] == "phase2-B-1files-r2-new"  # the round travels on
    alert.assert_called_once_with("new", "phase2-B-1files-r2-new")  # standard topic
    old_notice.assert_not_called()  # not also the old "batch submitted" notice
    status.assert_called_once_with("prev", "retrieved")
    ep._post_process.assert_not_called()


def test_a_failing_hand_off_alert_does_not_fail_the_settle(ep):
    _results(ep, pending_batch="new")
    _metrics(ep, "new", 1000)
    with (
        mock.patch.object(ep, "_enqueue_or_alert"),
        mock.patch.object(ep, "_alert_handed_off", side_effect=RuntimeError("sns")),
        mock.patch("src.utils.job_queue.get_active_jobs", return_value=[]),
        mock.patch("src.utils.job_queue._get_table"),
    ):
        ep._settle_phase2_task("phase2_extract.py", 75)  # no SystemExit


def test_a_hand_off_with_nothing_to_enqueue_fails_loudly(ep):
    _results(ep)  # no pending_batch
    with (
        mock.patch("src.utils.observe.observe_error") as err,
        pytest.raises(SystemExit) as stop,
    ):
        ep._settle_phase2_task("phase2_extract.py", 75)
    assert stop.value.code == 1
    assert err.call_args.args[1] == "handoff_not_enqueued"


def test_a_finished_book_is_post_processed(ep):
    with mock.patch("src.utils.job_queue.update_job_status") as status:
        ep._settle_phase2_task("phase2_extract.py", 0, retrieved_batch="prev")
    ep._post_process.assert_called_once()
    ep._final_sync.assert_called_once()
    status.assert_called_once_with("prev", "retrieved")


def test_a_failed_or_capped_run_enqueues_nothing_and_fails(ep):
    _results(ep, incomplete="batch rounds over cap")
    with (
        mock.patch.object(ep, "_enqueue_from_metrics") as enqueue,
        mock.patch("src.utils.job_queue.update_job_status") as status,
        pytest.raises(SystemExit) as stop,
    ):
        ep._settle_phase2_task("phase2_extract.py", 1, retrieved_batch="prev")
    assert stop.value.code == 1
    enqueue.assert_not_called()
    ep._post_process.assert_not_called()
    ep._final_sync_failed_phase.assert_called_once()
    status.assert_called_once_with("prev", "failed")
