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
        mock.patch("src.utils.observe.observe_error") as warn,
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


# --- observability --------------------------------------------------------------------------


def test_a_hand_off_alerts_the_book_and_round_on_the_standard_topic(ep, monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "USA-E-XChannel")
    monkeypatch.delenv("BATCH_IN_TASK_WAIT_MINUTES", raising=False)
    with mock.patch("src.utils.observe.publish_alert") as alert:
        ep._alert_handed_off("b-5", "phase2-USA-E-XChannel-15files-r3-812reqs")
    subject, text = alert.call_args.args
    assert "round 3 handed to the poller" in subject and "USA-E-XChannel" in subject
    assert "b-5" in text and "Nothing was retried live" in text


def test_the_hand_off_does_not_also_send_the_old_submitted_notice(
    ep, tmp_path, monkeypatch
):
    import json as _json

    monkeypatch.setattr(ep, "WORKDIR", tmp_path)
    (tmp_path / "output" / "metrics").mkdir(parents=True)
    (tmp_path / "output" / "metrics" / "batch_b-6_1.json").write_text(
        _json.dumps(
            {"batch_id": "b-6", "total_requests": 3, "batch_name": "n-r2-3reqs"}
        )
    )
    with (
        mock.patch.object(ep, "_enqueue_or_alert"),
        mock.patch.object(ep, "_notify_batch_submitted") as notice,
        mock.patch("src.utils.job_queue.get_active_jobs", return_value=[]),
        mock.patch("src.utils.job_queue._get_table"),
    ):
        assert ep._enqueue_from_metrics(
            "phase2_extract.py", batch_id="b-6", notify=False
        )
    notice.assert_not_called()


def test_a_finished_book_logs_one_rounds_summary():
    from src.utils import batch_rounds as br
    from src.utils.batch_api import BatchRequest

    def req(rid, kind):
        return BatchRequest(rid, [], "m", 0.1, kind)

    rounds = br.BatchRounds(scope="B", first=3)
    with mock.patch.object(br, "observe"):
        rounds.admit([req("a", "events"), req("b", "places")])
        rounds.admit([req("c", "equipment_refine")])
    with mock.patch.object(br, "observe") as obs:
        rounds.complete(125.0)
    kw = obs.call_args.kwargs
    assert obs.call_args.args[1] == "rounds_complete"
    assert (kw["rounds"], kw["this_task_rounds"], kw["this_task_requests"]) == (4, 2, 3)
    assert kw["resumed"] is True and kw["this_task_minutes"] == 2.1


def test_shared_requests_are_counted_and_logged(tmp_path, monkeypatch, caplog):
    import json as _json
    import threading
    import time as _time

    import src.grok_client as gc
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    token = gc.current_book.set(None)
    try:
        client = gc.GrokClient(DiskCacheBackend(tmp_path / "c"))

        def api(*a, **k):
            _time.sleep(0.2)
            return {"choices": [{"message": {"content": _json.dumps({"ok": 1})}}]}

        start = threading.Barrier(3)

        def ask():
            start.wait()
            client.extract_json("Same?", cache_type="equipment_disambiguation")

        with mock.patch.object(client, "_call_api", side_effect=api):
            threads = [threading.Thread(target=ask) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        assert client._flight_shared == 2
        with caplog.at_level("INFO"):
            client.log_cache_stats()
        assert "OBSERV grok.single_flight shared=2 asked_itself=0" in caplog.text
    finally:
        gc.current_book.reset(token)


def test_the_spot_handler_requeues_last_after_the_lock_and_lease(ep, monkeypatch):
    order = []
    for name in (
        "_final_sync",
        "_remove_lock",
        "_release_nat_lease",
        "_requeue_interrupted_resume",
    ):
        monkeypatch.setattr(ep, name, lambda *a, _n=name: order.append(_n))
    with pytest.raises(SystemExit):
        ep._handle_sigterm(15, None)
    assert order[-1] == "_requeue_interrupted_resume"
    assert order.index("_remove_lock") < order.index("_requeue_interrupted_resume")


def test_alerts_publish_with_short_timeouts(monkeypatch):
    from src.utils import observe

    monkeypatch.setenv("NOTIFICATION_TOPIC_ARN", "arn:aws:sns:us-east-1:1:t")
    with mock.patch("boto3.client") as client:
        assert observe.publish_alert("s", "m") is True
    cfg = client.call_args.kwargs["config"]
    assert cfg.connect_timeout == 3 and cfg.read_timeout == 5
