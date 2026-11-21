"""Group 2 step 2c: a Phase 3 batch round that is not answered in the task is handed to the batch
poller, and a retrieve task RESUMES the run. The hand-off is not a failure and not the end: the
attempt is given back, the lock freed, the book not relaunched while its batch is live; the resumed
task claims its own attempt, takes the lock and goes on with the next round."""

import json
import os
from unittest import mock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("ECS_CLUSTER", "test-wwii-pipeline")
os.environ.setdefault("PRIVATE_SUBNET_IDS", "subnet-abc123")
os.environ.setdefault("SECURITY_GROUP_ID", "sg-abc123")
os.environ.setdefault("CACHE_TABLE", "test-wwii-api-cache")
os.environ.setdefault("PHASE1_TASK_DEF", "test-wwii-phase1-parse")

import ecs_entrypoint as ee  # noqa: E402
from src.utils import job_queue  # noqa: E402

P3 = "phase3_enrich_data.py"


@pytest.fixture(autouse=True)
def _restore_module_state():
    """The resume code sets module globals (lock ownership, the batch being resumed)."""
    saved = (ee._lock_owned, ee._current_resume_batch, ee._current_phase_script)
    yield
    ee._lock_owned, ee._current_resume_batch, ee._current_phase_script = saved


@pytest.fixture
def ep(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    (tmp_path / "output" / "metrics").mkdir(parents=True)
    monkeypatch.setattr(ee, "_current_phase_script", P3)
    monkeypatch.setenv("BOOK_NAME", "BookA")
    calls = []
    for name in (
        "_final_sync",
        "_final_sync_failed_phase",
        "_stop_openserp_if_running",
        "_post_process",
        "_audit_failed_phase2",
        "_remove_lock",
        "_release_nat_lease",
        "_reset_spot_ladder",
        "_give_back_own_attempt",
        "_attempt_counts",
        "_notify_failure",
    ):
        m = mock.Mock(side_effect=lambda *a, _n=name, **k: calls.append(_n))
        monkeypatch.setattr(ee, name, m)
    monkeypatch.setattr(ee, "_defer_on_stop_cause", lambda rc: "next step")
    return calls


def _results(ep_dir, **body):
    (ee.WORKDIR / "output" / ".phase_results.json").write_text(json.dumps(body))


def test_a_handed_off_phase3_round_is_enqueued_and_the_task_leaves_cleanly(ep):
    _results(ee, pending_batch="b9", pending_batch_name="phase3-BookA-r2-40reqs")
    with (
        mock.patch.object(ee, "_enqueue_from_metrics", return_value=True) as enq,
        mock.patch.object(ee, "_alert_handed_off") as alert,
    ):
        ee._settle_phase2_task(P3, 75)  # no SystemExit
    enq.assert_called_once_with(P3, batch_id="b9", notify=False)
    alert.assert_called_once_with("b9", "phase3-BookA-r2-40reqs", P3)
    # saved, then: attempt given back, lock freed, NAT demand dropped; NOT post-processed
    assert "_final_sync" in ep and "_post_process" not in ep
    assert ep.index("_give_back_own_attempt") < ep.index("_remove_lock")
    assert "_release_nat_lease" in ep and "_attempt_counts" not in ep


def test_a_hand_off_that_could_not_be_enqueued_frees_the_lock_and_fails_loudly(ep):
    _results(ee, pending_batch="b9")
    with (
        mock.patch.object(ee, "_enqueue_from_metrics", return_value=False),
        mock.patch("src.utils.observe.observe_error") as err,
        pytest.raises(SystemExit) as stop,
    ):
        ee._settle_phase2_task(P3, 75)
    assert stop.value.code == 1
    assert err.call_args.args[1] == "handoff_not_enqueued"
    assert "_remove_lock" in ep


def test_a_finished_phase3_run_is_post_processed_and_releases_its_resources(ep):
    ee._settle_phase2_task(P3, 0, retrieved_batch="")
    assert ep == [
        "_final_sync",
        "_stop_openserp_if_running",
        "_post_process",
        "_reset_spot_ladder",
        "_release_nat_lease",
    ]


def test_a_failed_phase3_run_takes_the_one_failure_path(ep):
    """The same path as a failure in run_phase: defer or count, notify, sync, free the lock."""
    with pytest.raises(SystemExit) as stop:
        ee._settle_phase2_task(P3, 82)  # a step failed
    assert stop.value.code == 82
    assert ep == [
        "_attempt_counts",
        "_notify_failure",
        "_final_sync_failed_phase",
        "_audit_failed_phase2",
        "_remove_lock",
        "_release_nat_lease",
    ]


def test_a_phase2_failure_is_unchanged(ep):
    with pytest.raises(SystemExit) as stop:
        ee._settle_phase2_task("phase2_extract.py", 1)
    assert stop.value.code == 1
    assert "_notify_failure" not in ep and "_attempt_counts" not in ep


def test_the_resumed_task_continues_the_round_count_for_both_phases():
    for script in (P3, "phase2_extract.py"):
        env = ee._retrieve_rerun_env(script, "phase3-BookA-r3-12reqs")
        assert env["BATCH_ROUND_START"] == "4" and env["SKIP_RETRY"] == "1"
    other = ee._retrieve_rerun_env("phase1_parse.py", "x")
    assert "BATCH_ROUND_START" not in other
    assert ee._resumes_rounds(P3) and not ee._resumes_rounds("phase1_parse.py")


def test_the_handed_off_alert_names_phase_3(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "src.utils.observe.publish_alert", lambda s, m: sent.append((s, m))
    )
    monkeypatch.setenv("BOOK_NAME", "BookA")
    ee._alert_handed_off("b9", "phase3-BookA-r2-40reqs", P3)
    subject, text = sent[0]
    assert "Phase 3 round 2 handed to the poller (BookA)" in subject
    assert text.startswith("Phase 3 for BookA")
    ee._alert_handed_off("b8", "phase2-BookA-1files-r1-9reqs")
    assert "Phase 2 round 1" in sent[1][0]


# --- the resume ----------------------------------------------------------------------------------


def _resume_mocks(monkeypatch, claim=True, lock=True):
    order = []
    monkeypatch.setattr(
        ee, "_claim_enrich_attempt", lambda: order.append("claim") or claim
    )
    monkeypatch.setattr(ee, "_acquire_nat_lease", lambda: order.append("nat"))
    monkeypatch.setattr(ee, "_acquire_lock", lambda ph: order.append("lock") or lock)
    monkeypatch.setattr(
        ee, "_preflight_balance_check", lambda ph: order.append("balance")
    )
    monkeypatch.setattr(ee, "_preflight_credit_check", lambda: order.append("credit"))
    monkeypatch.setattr(
        ee, "_start_openserp_if_needed", lambda ph: order.append("openserp")
    )
    monkeypatch.setattr(ee, "_remove_lock", lambda ph: order.append("remove_lock"))
    monkeypatch.setattr(ee, "_give_back_own_attempt", lambda: order.append("give_back"))
    monkeypatch.setattr(ee, "_release_nat_lease", lambda: order.append("release_nat"))
    monkeypatch.setattr(ee, "PHASE3_RESUME_LOCK_WAIT_S", 0)
    return order


def test_a_resumed_phase3_prepares_like_the_start_of_a_run(monkeypatch):
    order = _resume_mocks(monkeypatch)
    assert ee._begin_phase3_resume(P3) is True
    assert order == ["claim", "nat", "lock", "balance", "credit", "openserp"]


def test_a_resume_past_the_attempt_cap_ends_without_touching_any_lock(monkeypatch):
    """The retrieve task holds no lock yet: removing one would delete another run's."""
    order = _resume_mocks(monkeypatch, claim=False)
    assert ee._begin_phase3_resume(P3) is False
    assert order == ["claim"]  # no spend, no NAT, no lock removed, nothing started


def test_a_resume_that_cannot_get_the_lock_leaves_the_book_queued(monkeypatch, caplog):
    import logging

    order = _resume_mocks(monkeypatch, lock=False)
    with caplog.at_level(logging.INFO):
        assert ee._begin_phase3_resume(P3) is False
    assert order == ["claim", "nat", "lock", "give_back", "release_nat"]
    assert "batch.resume_waiting" in caplog.text


def test_the_lock_wait_polls_keeps_the_nat_lease_alive_and_takes_ownership(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(ee, "_acquire_lock", lambda ph: next(answers))
    monkeypatch.setattr(ee, "PHASE3_RESUME_LOCK_WAIT_S", 3600)
    monkeypatch.setattr(ee, "_lock_owned", False)
    sleeps, beats = [], []
    monkeypatch.setattr(ee.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(ee, "_heartbeat_nat_lease", lambda: beats.append(1))
    assert ee._wait_for_lock(P3) is True
    assert (
        len(sleeps) == 2 and len(beats) == 2
    )  # the 15-min lease outlives a 30-min wait
    assert ee._lock_owned is True


def test_a_task_that_never_took_the_lock_does_not_remove_it(monkeypatch):
    removed = []
    fake = mock.Mock()
    fake.Table.return_value.delete_item.side_effect = lambda **k: removed.append(k)
    monkeypatch.setattr(ee.boto3, "resource", lambda *a, **k: fake)
    monkeypatch.setattr(ee, "_released_locks", set())
    monkeypatch.setattr(ee, "_lock_owned", False)
    ee._remove_lock(P3)  # e.g. a SIGTERM during the wait
    assert removed == []
    monkeypatch.setattr(ee, "_lock_owned", True)
    ee._remove_lock(P3)
    assert len(removed) == 1


def test_a_failed_preflight_after_the_lock_frees_it(monkeypatch):
    order = _resume_mocks(monkeypatch)

    def boom(ph):
        raise RuntimeError("x")

    monkeypatch.setattr(ee, "_start_openserp_if_needed", boom)
    with pytest.raises(RuntimeError):
        ee._begin_phase3_resume(P3)
    assert order[-2:] == ["remove_lock", "release_nat"]


def test_a_hand_off_nothing_can_resume_keeps_the_attempt_counted(ep):
    _results(ee, pending_batch="b9")
    with (
        mock.patch.object(ee, "_enqueue_from_metrics", return_value=False),
        mock.patch("src.utils.observe.observe_error"),
        pytest.raises(SystemExit),
    ):
        ee._settle_phase2_task(P3, 75)
    assert "_give_back_own_attempt" not in ep and "_remove_lock" in ep


def test_a_settled_phase3_failure_is_logged_under_the_phase_stem(ep, caplog):
    import logging

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        ee._settle_phase2_task(P3, 82)
    rec = next(r for r in caplog.records if "Phase failed" in r.getMessage())
    assert rec.extra_fields["phase"] == "phase3_enrich_data"


def _retrieve_mocks(monkeypatch, begin=True):
    order = []

    def rec(name):
        return lambda *a, **k: order.append(name)

    job = mock.Mock(
        book="BookA",
        batch_name="phase3-BookA-r2-9reqs",
        phase="phase3",
        request_count=9,
    )
    monkeypatch.setattr("src.utils.job_queue.get_job", lambda b: job)
    monkeypatch.setattr(
        "src.utils.job_queue.update_job_status", lambda b, st: order.append(f"job:{st}")
    )
    for n in (
        "_load_secrets",
        "_patch_config",
        "_ensure_nat_if_needed",
        "_setup_symlinks",
        "_download_inputs",
        "_stamp_schema_versions",
        "_download_s3_prefix",
        "_cache_batch_answers",
    ):
        monkeypatch.setattr(ee, n, rec(n))
    monkeypatch.setattr(ee, "_s3_client", lambda: None)
    monkeypatch.setattr(
        ee, "_begin_phase3_resume", lambda ph: order.append("begin") or begin
    )
    monkeypatch.setattr(ee, "_start_balance_monitor", lambda ph: None)
    monkeypatch.setattr(
        ee, "_settle_phase2_task", lambda *a, **k: order.append(("settle", k))
    )
    ran = []

    def run(cmd, **k):
        order.append("run")
        ran.append((cmd, k["env"]))
        return mock.Mock(returncode=0)

    monkeypatch.setattr(ee.subprocess, "run", run)
    monkeypatch.setattr(ee, "BackgroundSync", lambda *a: mock.Mock())
    return order, ran


def test_a_phase3_retrieve_takes_the_lock_before_it_downloads_the_inputs(monkeypatch):
    order, ran = _retrieve_mocks(monkeypatch)
    ee.run_retrieve_only(P3, [], "b9")
    assert order == [
        "_load_secrets",
        "_patch_config",
        "_ensure_nat_if_needed",
        "_setup_symlinks",
        "_download_s3_prefix",
        "_cache_batch_answers",
        "job:retrieved",
        "begin",
        "_download_inputs",
        "_stamp_schema_versions",
        "run",
        ("settle", {"retrieved_batch": "b9"}),
    ]
    cmd, env = ran[0]
    assert "--batch" in cmd and env["BATCH_ROUND_START"] == "3"  # resumes at round 3


def test_a_phase3_retrieve_that_cannot_start_downloads_and_runs_nothing(monkeypatch):
    order, ran = _retrieve_mocks(monkeypatch, begin=False)
    ee.run_retrieve_only(P3, [], "b9")
    assert "_download_inputs" not in order and ran == []
    assert (
        "job:retrieved" in order
    )  # the answers are cached: the queued book resumes from them
    assert ee._current_resume_batch == ""


def test_phase2_retrieve_keeps_its_order(monkeypatch):
    order, ran = _retrieve_mocks(monkeypatch)
    ee.run_retrieve_only("phase2_extract.py", [], "b9")
    assert order.index("_download_inputs") < order.index("_cache_batch_answers")
    assert "begin" not in order


def test_a_job_row_with_an_unsafe_book_is_not_retrieved(monkeypatch):
    order, ran = _retrieve_mocks(monkeypatch)
    job = mock.Mock(book="../etc", batch_name="n", phase="phase3", request_count=1)
    monkeypatch.setattr("src.utils.job_queue.get_job", lambda b: job)
    with (
        mock.patch("src.utils.observe.observe_error") as err,
        pytest.raises(SystemExit),
    ):
        ee.run_retrieve_only(P3, [], "b9")
    assert err.call_args.args[1] == "retrieve_book_rejected"
    assert "job:failed" in order and "_download_inputs" not in order and ran == []


# --- the queue ------------------------------------------------------------------------------------


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        yield ddb.create_table(
            TableName="test-wwii-api-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


def _job(table, batch_id, phase, book, status):
    table.put_item(
        Item={
            "cache_key": f"batch_job#{batch_id}",
            "batch_id": batch_id,
            "phase": phase,
            "book": book,
            "status": status,
            "submitted_at": int(__import__("time").time()),
        }
    )


def test_a_book_with_a_live_phase3_batch_is_found_and_others_are_not(table):
    _job(table, "b1", "phase3", "BookA", "pending")
    _job(table, "b2", "phase3", "BookB", "retrieved")  # done
    _job(table, "b3", "phase2", "BookC", "pending")  # another phase
    _job(table, "b4", "phase3", "BookD", "failed")
    assert job_queue.live_batch_for(table, "phase3", "BookA") == "b1"
    for book in ("BookB", "BookC", "BookD", "Nope"):
        assert job_queue.live_batch_for(table, "phase3", book) is None
    for status in ("ready", "in_task"):
        _job(table, f"x{status}", "phase3", f"Book{status}", status)
        assert job_queue.live_batch_for(table, "phase3", f"Book{status}")


def test_in_task_records_exist_for_phase3_and_only_for_the_rounds_phases(
    table, monkeypatch
):
    monkeypatch.setenv("ECS_TASK_ID", "t1")
    monkeypatch.setenv("BOOK_NAME", "BookA")
    monkeypatch.setattr(job_queue, "_get_table", lambda: table)
    monkeypatch.setenv("PIPELINE_PHASE", "phase3_enrich_data")
    job_queue.record_in_task("b1", "phase3-BookA-r1-9reqs", 9)
    item = table.get_item(Key={"cache_key": "batch_job#b1"})["Item"]
    assert (item["phase"], item["status"]) == ("phase3", "in_task")
    job_queue.mark_retrieved_in_task("b1")
    assert (
        table.get_item(Key={"cache_key": "batch_job#b1"})["Item"]["status"]
        == "retrieved"
    )
    monkeypatch.setenv("PIPELINE_PHASE", "phase2_extract")
    job_queue.record_in_task("b2", "n", 1)
    assert (
        table.get_item(Key={"cache_key": "batch_job#b2"})["Item"]["phase"] == "phase2"
    )
    monkeypatch.setenv("PIPELINE_PHASE", "phase1_parse")
    job_queue.record_in_task("b3", "n", 1)
    assert "Item" not in table.get_item(Key={"cache_key": "batch_job#b3"})


def test_the_trigger_does_not_relaunch_a_book_whose_batch_is_live(table):
    from lambda_handlers import trigger_handler as th

    _job(table, "b1", "phase3", "BookA", "in_task")
    with (
        mock.patch.object(th, "dynamo", table),
        mock.patch.object(th, "_held_for_credit") as held,
        mock.patch.object(th, "_acquire_task_lock") as lock,
    ):
        assert th._run_task(th.PHASE3_TASK_DEF, "reconcile", book_name="BookA") is False
    held.assert_not_called()
    lock.assert_not_called()  # no lock, no launch: the retrieve task resumes the run


def test_an_old_or_expired_live_row_does_not_block_its_book_and_is_reported(table):
    import time

    now = int(time.time())
    for bid, age, ttl in (("old", 40 * 3600, now + 99999), ("exp", 3600, now - 5)):
        table.put_item(
            Item={
                "cache_key": f"batch_job#{bid}",
                "batch_id": bid,
                "phase": "phase3",
                "book": "BookA",
                "status": "pending",
                "submitted_at": now - age,
                "ttl": ttl,
            }
        )
    with mock.patch("src.utils.observe.observe_warn") as warn:
        assert job_queue.live_batch_for(table, "phase3", "BookA") is None
    assert warn.call_count == 2 and warn.call_args.args[1] == "live_row_ignored"


def test_an_unscoped_launch_waits_for_any_live_phase3_batch_and_manual_overrides(table):
    from lambda_handlers import trigger_handler as th

    _job(table, "b1", "phase3", "BookA", "pending")
    with mock.patch.object(th, "dynamo", table):
        assert th._phase3_batch_live("", "s3-event") is True  # no book: any live batch
        assert th._phase3_batch_live("BookB", "reconcile") is False
        assert (
            th._phase3_batch_live("BookA", "manual") is False
        )  # the operator's override


def test_a_failed_in_task_record_is_an_error_naming_the_double_pay_risk(monkeypatch):
    monkeypatch.setenv("ECS_TASK_ID", "t1")
    monkeypatch.setenv("PIPELINE_PHASE", "phase3_enrich_data")

    def boom(job):
        raise RuntimeError("x")

    monkeypatch.setattr(job_queue, "enqueue_job", boom)
    with mock.patch("src.utils.observe.observe_error") as err:
        job_queue.record_in_task("b1", "n", 1)
    assert err.call_args.args[1] == "in_task_record_failed"
    assert "SAME requests" in err.call_args.kwargs["note"]


def test_a_sigterm_mid_resume_does_not_requeue_a_batch_when_a_later_round_is_live(
    monkeypatch, table
):
    monkeypatch.setattr(ee, "_current_resume_batch", "old")
    monkeypatch.setattr(ee, "_current_phase_script", P3)
    monkeypatch.setenv("BOOK_NAME", "BookA")
    monkeypatch.setattr(job_queue, "_get_table", lambda: table)
    _job(table, "old", "phase3", "BookA", "retrieved")
    _job(table, "new", "phase3", "BookA", "in_task")
    with mock.patch("src.utils.observe.observe_error") as err:
        ee._requeue_interrupted_resume()
    assert err.call_count == 0
    assert (
        table.get_item(Key={"cache_key": "batch_job#old"})["Item"]["status"]
        == "retrieved"
    )
    table.delete_item(Key={"cache_key": "batch_job#new"})
    with mock.patch("src.utils.observe.observe_error"):
        ee._requeue_interrupted_resume()
    assert (
        table.get_item(Key={"cache_key": "batch_job#old"})["Item"]["status"] == "ready"
    )


def test_the_trigger_launches_a_book_with_no_live_batch_and_when_the_lookup_fails(
    table,
):
    from lambda_handlers import trigger_handler as th

    with mock.patch.object(th, "dynamo", table):
        assert th._phase3_batch_live("BookA") is False
    broken = mock.Mock()
    broken.scan.side_effect = RuntimeError("throttled")
    with mock.patch.object(th, "dynamo", broken):
        assert th._phase3_batch_live("BookA") is False  # a double launch is only a cost


def test_the_rounds_wait_is_the_shared_in_task_wait(tmp_path, monkeypatch):
    """Phase 3 waits for a round like Phase 2 (batch.in_task_wait_minutes), then hands it off."""
    import phase3_enrich_data as P
    from src.utils.batch_api import BatchCollector, BatchRequest, in_task_wait_minutes

    waits = []

    class G:
        batch_mode = True
        _batch_collector = BatchCollector()

        def submit_batch(self, batch_name, max_wait_minutes=None):
            waits.append(max_wait_minutes)
            self._batch_collector = None
            return "b1"

    g = G()
    g._batch_collector.add(
        BatchRequest("k", [{"role": "user", "content": "k"}], "m", 0.1, "x")
    )
    monkeypatch.setattr(P, "_run_enrichment_steps", lambda *a: 0)
    cfg = {"batch": {"in_task_wait_minutes": 7}}
    args = mock.Mock(output_dir=tmp_path)
    P._run_batch_rounds(args, cfg, g, {}, 1, 0)
    assert waits == [in_task_wait_minutes(cfg)] == [7.0]


def test_a_phase3_retrieve_task_owns_no_lock_from_its_first_line(monkeypatch):
    """A NAT abort or a SIGTERM before the lock wait must not delete another run's lock."""
    order, _ran = _retrieve_mocks(monkeypatch)
    monkeypatch.setattr(
        ee, "_lock_owned", True
    )  # the module default (a trigger-launched task)
    seen = []

    def nat_abort():
        seen.append(ee._lock_owned)
        raise SystemExit(1)

    monkeypatch.setattr(ee, "_ensure_nat_if_needed", lambda ph: nat_abort())
    removed = []
    fake = mock.Mock()
    fake.Table.return_value.delete_item.side_effect = lambda **k: removed.append(k)
    monkeypatch.setattr(ee.boto3, "resource", lambda *a, **k: fake)
    monkeypatch.setattr(ee, "_released_locks", set())
    with pytest.raises(SystemExit):
        ee.run_retrieve_only(P3, [], "b9")
    assert seen == [False]
    ee._remove_lock(P3)  # what the NAT abort / _handle_sigterm call
    assert removed == []


def test_a_phase2_retrieve_task_keeps_its_lock_behaviour(monkeypatch):
    order, _ran = _retrieve_mocks(monkeypatch)
    monkeypatch.setattr(ee, "_lock_owned", True)
    ee.run_retrieve_only("phase2_extract.py", [], "b9")
    assert ee._lock_owned is True
