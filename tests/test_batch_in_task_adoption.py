"""A batch a task is waiting for is recorded as ``in_task`` the moment it is submitted, so a task
killed during its 90-min wait (spot reclaim) does not orphan it: the poller leaves an in_task job
alone while the wait can still be running, then adopts it as pending (alerted)."""

import time
from unittest import mock

import pytest

from src.utils import job_queue as jq


@pytest.fixture
def ecs(monkeypatch):
    monkeypatch.setenv("ECS_TASK_ID", "t-1")
    monkeypatch.setenv("PIPELINE_PHASE", "phase2_extract")
    monkeypatch.setenv("BOOK_NAME", "USA-E-XChannel")


def test_a_phase2_batch_is_recorded_in_task_at_submit(ecs):
    with mock.patch.object(jq, "enqueue_job") as put:
        jq.record_in_task("b-1", "phase2-USA-E-XChannel-1files-r2-9reqs", 9)
    job = put.call_args.args[0]
    assert (job.batch_id, job.status, job.book, job.request_count) == (
        "b-1",
        "in_task",
        "USA-E-XChannel",
        9,
    )


def test_nothing_is_recorded_outside_ecs_or_phase2(monkeypatch):
    monkeypatch.delenv("ECS_TASK_ID", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.setenv("PIPELINE_PHASE", "phase2_extract")
    with mock.patch.object(jq, "enqueue_job") as put:
        jq.record_in_task("b-1", "n", 1)
        monkeypatch.setenv("ECS_TASK_ID", "t-1")
        monkeypatch.setenv("PIPELINE_PHASE", "phase3_enrich_data")
        jq.record_in_task("b-1", "n", 1)
    put.assert_not_called()


def test_the_retrieved_mark_never_creates_a_row_for_other_phases(monkeypatch):
    monkeypatch.setenv("ECS_TASK_ID", "t-1")
    monkeypatch.setenv("PIPELINE_PHASE", "phase3_enrich_data")
    with mock.patch.object(jq, "update_job_status") as upd:
        jq.mark_retrieved_in_task("b-1")
    upd.assert_not_called()


@pytest.fixture
def poller(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from lambda_handlers import batch_poller

    return batch_poller


def _job(age_s, status="in_task"):
    return {
        "cache_key": "batch_job#b-1",
        "batch_id": "b-1",
        "book": "B",
        "status": status,
        "submitted_at": int(time.time()) - age_s,
    }


def test_an_in_task_job_is_left_to_its_task_while_it_may_still_be_waiting(poller):
    table = mock.Mock()
    assert (
        poller._pollable(_job(60 * 60), table) is False
    )  # 1 h: within the 90-min wait
    table.update_item.assert_not_called()
    assert poller._pollable(_job(0, status="pending"), table) is True


def test_an_orphaned_in_task_job_is_adopted_and_alerted(poller):
    table = mock.Mock()
    job = _job(3 * 3600)
    with mock.patch.object(poller, "observe_error") as err:
        assert poller._pollable(job, table) is True
    assert job["status"] == "pending"
    kwargs = table.update_item.call_args.kwargs
    assert kwargs["ConditionExpression"] == "#s = :in_task"  # only if still in_task
    assert err.call_args.args[1] == "in_task_adopted"


def test_a_job_its_task_settled_meanwhile_is_not_adopted(poller):
    table = mock.Mock()
    table.update_item.side_effect = RuntimeError("ConditionalCheckFailed")
    assert poller._pollable(_job(3 * 3600), table) is False


def test_the_adopt_age_outlasts_the_in_task_wait(poller):
    from src.utils.batch_api import DEFAULT_IN_TASK_WAIT_MINUTES

    assert poller._IN_TASK_ADOPT_S > DEFAULT_IN_TASK_WAIT_MINUTES * 60


def test_the_round_survives_a_truncated_batch_name():
    from src.utils.batch_rounds import round_of

    base = "phase2-" + "-".join(f"Book{i}" for i in range(40)) + "-40files"
    suffix = "-r6-1200reqs"
    name = base[: 128 - len(suffix)] + suffix
    assert len(name) <= 128 and round_of(name) == 6


# --- review follow-ups -----------------------------------------------------------------------


def test_a_wait_past_the_adopt_age_is_clamped(monkeypatch):
    from src.utils import batch_api

    monkeypatch.setenv("BATCH_IN_TASK_WAIT_MINUTES", "240")
    assert batch_api.in_task_wait_minutes({}) == batch_api.MAX_IN_TASK_WAIT_MINUTES
    from lambda_handlers import batch_poller

    assert batch_poller._IN_TASK_ADOPT_S > batch_api.MAX_IN_TASK_WAIT_MINUTES * 60


def test_settling_an_in_task_row_never_creates_one(ecs):
    table = mock.Mock()
    with mock.patch.object(jq, "_get_table", return_value=table):
        jq.mark_in_task("b-1", "failed")
    assert table.update_item.call_args.kwargs["ConditionExpression"] == (
        "attribute_exists(cache_key)"
    )


def test_a_model_stop_marks_the_in_task_batch_failed_not_adoptable(
    tmp_path, monkeypatch, ecs
):
    """A deliberate stop must not be adopted after 2 h and retrieved every 15 min."""
    import src.grok_client as gc
    from src.utils import batch_api, credit_gate
    from src.utils.cache_backend import DiskCacheBackend
    from src.utils.grok_models import GrokModelUnavailable

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    client = gc.GrokClient(DiskCacheBackend(tmp_path / "c"), batch_mode=True)
    client.cache_dir = tmp_path
    client._batch_collector.add(
        batch_api.BatchRequest(
            "k1", [{"role": "user", "content": "x"}], "m", 0.1, "places"
        )
    )
    monkeypatch.setattr(gc, "_save_metrics", lambda m: None)
    monkeypatch.setattr(credit_gate, "reserve", lambda *a, **k: True)
    monkeypatch.setattr(credit_gate, "release", lambda *a, **k: None)
    with (
        mock.patch.object(batch_api, "submit_batch", return_value="b-8"),
        mock.patch.object(batch_api, "poll_batch", return_value={"state": {}}),
        mock.patch.object(batch_api, "retrieve_results_ordered", return_value=[]),
        mock.patch.object(
            batch_api, "verify_served_models", side_effect=GrokModelUnavailable("gone")
        ),
        mock.patch.object(client, "_estimate_batch_cost", return_value=0.0),
        mock.patch.object(jq, "enqueue_job"),
        mock.patch.object(jq, "mark_in_task") as mark,
        pytest.raises(GrokModelUnavailable),
    ):
        client.submit_batch("phase2-B-1files-r1-1reqs", max_wait_minutes=90)
    mark.assert_called_once_with("b-8", "failed")


@pytest.fixture
def ep(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    import ecs_entrypoint

    return ecs_entrypoint


def test_a_resumed_run_stopped_mid_run_goes_back_to_the_poller(ep, monkeypatch):
    monkeypatch.setattr(ep, "_current_resume_batch", "b-3")
    with (
        mock.patch("src.utils.job_queue.update_job_status") as status,
        mock.patch("src.utils.observe.observe_warn") as warn,
    ):
        ep._requeue_interrupted_resume()
    status.assert_called_once_with("b-3", "ready")
    assert warn.call_args.args[1] == "resume_interrupted"


def test_nothing_is_requeued_outside_a_resumed_run(ep, monkeypatch):
    monkeypatch.setattr(ep, "_current_resume_batch", "")
    with mock.patch("src.utils.job_queue.update_job_status") as status:
        ep._requeue_interrupted_resume()
    status.assert_not_called()


def test_the_submitted_ids_fall_back_to_the_dynamo_metrics_copy(ep):
    import json as _json

    table = mock.Mock()
    table.get_item.return_value = {
        "Item": {
            "response": _json.dumps(
                {"submitted_request_ids": [["k1", "places"], ["k2", "dates"]]}
            )
        }
    }
    with mock.patch("src.utils.job_queue._get_table", return_value=table):
        assert ep._submitted_from_dynamo_metrics("b-4") == [
            ("k1", "places"),
            ("k2", "dates"),
        ]
    table.get_item.assert_called_once_with(Key={"cache_key": "metrics#b-4"})
