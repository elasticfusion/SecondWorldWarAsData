"""Task 1.1b follow-ups (2026-11-19): a task-stop alert SNS never takes is not lost. The failed
report keeps its facts and is listed in one small item; the hourly check re-sends it once SNS
works again. An async trigger event that fails every retry lands in a DLQ that is alarmed, and
the trigger's error alarm fires on one error. A pipeline task may not write the trigger's launch
state (Fargate prices, stop reports) or climb a Spot ladder."""

from pathlib import Path

import pytest
import yaml

from src.utils import task_marker
from tests.test_spot_ladder import _CfnLoader, _stopped, table, trigger  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _deliveries(trigger, monkeypatch, outcomes):
    """observe_alert answers from ``outcomes`` (True = SNS took it); returns what was sent."""
    sent = []
    answers = iter(outcomes)

    def capture(s, e, note="", **k):
        ok = next(answers)
        sent.append((e, ok))
        return ok

    monkeypatch.setattr(trigger, "observe_alert", capture)
    return sent


def _failed_list(table):
    item = table.get_item(Key={"cache_key": "stopevt#failed"}).get("Item") or {}
    return set(item.get("tasks") or ())


def _fail_report(trigger, task="t1"):
    with pytest.raises(trigger.StopAlertUndelivered):
        trigger._report_task_stop(
            _stopped(code="EssentialContainerExited", exit_code=1, task=task)["detail"]
        )


def test_an_undelivered_stop_alert_is_resent_by_the_hourly_sweep(
    trigger, table, monkeypatch
):
    sent = _deliveries(trigger, monkeypatch, [False, False, True])
    _fail_report(trigger)
    assert _failed_list(table) == {"t1"}
    row = table.get_item(Key={"cache_key": "stopevt#t1"})["Item"]
    assert row["status"] == "failed" and '"task_id": "t1"' in row["facts"]
    assert trigger._sweep_failed_stop_reports() == 0  # SNS still down: kept
    assert _failed_list(table) == {"t1"}
    assert trigger._sweep_failed_stop_reports() == 1  # SNS back: sent once
    assert [e for e, _ in sent] == ["task_stopped_unreported"] * 3
    assert _failed_list(table) == set()
    assert table.get_item(Key={"cache_key": "stopevt#t1"})["Item"]["status"] == "done"
    assert trigger._sweep_failed_stop_reports() == 0  # nothing left


def test_a_stop_a_lambda_retry_reported_is_dropped_from_the_list(
    trigger, table, monkeypatch
):
    sent = _deliveries(trigger, monkeypatch, [False, True])
    _fail_report(trigger)
    trigger._report_task_stop(  # Lambda's own async retry, SNS back
        _stopped(code="EssentialContainerExited", exit_code=1)["detail"]
    )
    assert trigger._sweep_failed_stop_reports() == 0
    assert _failed_list(table) == set() and len(sent) == 2


def test_a_sweep_that_died_mid_report_is_retried_once_stale(
    trigger, table, monkeypatch
):
    """The claim keeps the stored facts, so a sweep killed mid-report (timeout, a DynamoDB
    error) leaves a stale "started" row the next sweep re-sends - not a silent 7-day wait.
    """
    sent = _deliveries(trigger, monkeypatch, [False, True])
    _fail_report(trigger)
    real = trigger._report_facts
    monkeypatch.setattr(
        trigger, "_report_facts", lambda f: (_ for _ in ()).throw(RuntimeError("ddb"))
    )
    assert trigger._sweep_failed_stop_reports() == 0  # died mid-report, logged
    row = table.get_item(Key={"cache_key": "stopevt#t1"})["Item"]
    assert row["status"] == "started" and "facts" in row
    monkeypatch.setattr(trigger, "_report_facts", real)
    assert trigger._sweep_failed_stop_reports() == 0  # in progress (not stale yet)
    # a float, as time.time() returns (the row's number is a Decimal)
    later = float(int(row["started_at"]) + trigger.STOP_REPORT_STALE_S + 1)
    monkeypatch.setattr(trigger.time, "time", lambda: later)
    assert trigger._sweep_failed_stop_reports() == 1
    assert len(sent) == 2 and _failed_list(table) == set()


def test_one_bad_id_does_not_block_the_rest(trigger, table, monkeypatch, caplog):
    _deliveries(trigger, monkeypatch, [False, False, True])
    _fail_report(trigger, "a1")
    _fail_report(trigger, "a2")
    real = trigger._resend_failed_stop

    def flaky(task_id):
        if task_id == "a1":
            raise RuntimeError("ddb")
        return real(task_id)

    monkeypatch.setattr(trigger, "_resend_failed_stop", flaky)
    assert trigger._sweep_failed_stop_reports() == 1
    assert _failed_list(table) == {"a1"} and "OBSERV stop.resend_failed" in caplog.text


def test_the_stop_event_handler_does_not_fail_for_an_undelivered_alert(
    trigger, table, monkeypatch
):
    _deliveries(trigger, monkeypatch, [False])
    out = trigger.handler(_stopped(code="EssentialContainerExited", exit_code=1), None)
    assert out == {"action": "checked"} and _failed_list(table) == {"t1"}


def test_the_sweep_stops_at_the_first_alert_that_still_fails(
    trigger, table, monkeypatch
):
    sent = _deliveries(trigger, monkeypatch, [False, False, False])
    _fail_report(trigger, "a1")
    _fail_report(trigger, "a2")
    assert trigger._sweep_failed_stop_reports() == 0
    assert len(sent) == 3 and _failed_list(table) == {"a1", "a2"}


def test_a_stored_report_that_is_gone_or_forged_is_alerted_not_replayed(
    trigger, table, monkeypatch
):
    sent = _deliveries(trigger, monkeypatch, [])  # nothing may be re-sent
    lost = []
    monkeypatch.setattr(
        trigger, "observe_error", lambda s, e, **k: lost.append((e, k["task"]))
    )
    table.put_item(Item={"cache_key": "stopevt#failed", "tasks": {"gone", "forged"}})
    table.put_item(  # facts naming another task (or a family this Lambda does not launch)
        Item={
            "cache_key": "stopevt#forged",
            "status": "failed",
            "facts": '{"task_id": "x"}',
        }
    )
    assert trigger._sweep_failed_stop_reports() == 0
    assert sent == [] and sorted(lost) == [
        ("stop_report_lost", "forged"),
        ("stop_report_lost", "gone"),
    ]
    assert _failed_list(table) == set()


def test_the_scheduled_check_runs_the_sweep_and_survives_its_failure(
    trigger, monkeypatch, caplog
):
    def boom():
        raise RuntimeError("ddb")

    monkeypatch.setattr(trigger, "_sweep_failed_stop_reports", boom)
    assert trigger.handler({"source": "scheduled"}, None) == {"action": "checked"}
    assert "OBSERV stop.sweep_failed" in caplog.text


def test_the_relayed_task_alert_is_resent_too(trigger, table, monkeypatch):
    sent = _deliveries(trigger, monkeypatch, [False, True])
    task_marker.mark(table, "t1", unsent_event="nat_not_ready", unsent_note="NAT down")
    _fail_report(trigger)
    trigger._sweep_failed_stop_reports()
    assert [e for e, _ in sent] == ["task_alert_relayed"] * 2


# --- infrastructure --------------------------------------------------------------------------


def _resources(name):
    text = (ROOT / "cloudformation" / name).read_text(encoding="utf-8")
    return yaml.load(text, Loader=_CfnLoader)["Resources"]


def test_a_trigger_event_that_fails_every_retry_lands_in_an_alarmed_dlq():
    compute = _resources("compute.yaml")
    assert "DeadLetterConfig" in compute["TriggerFunction"]["Properties"]
    assert compute["TriggerDLQ"]["Properties"]["MessageRetentionPeriod"] == 1209600
    events = _resources("events.yaml")
    alarm = events["TriggerDLQAlarm"]["Properties"]
    assert alarm["MetricName"] == "ApproximateNumberOfMessagesVisible"
    assert alarm["Threshold"] == 1 and alarm["AlarmActions"]


def test_one_trigger_error_raises_the_alarm():
    alarm = _resources("events.yaml")["Phase1ErrorAlarm"]["Properties"]
    assert alarm["Threshold"] == 1 and alarm["TreatMissingData"] == "notBreaching"


def test_the_trigger_may_send_to_its_dlq():
    """The DLQ name matches LambdaExecutionRole's <env>-wwii-*-dlq SendMessage grant."""
    text = (ROOT / "cloudformation" / "iam.yaml").read_text(encoding="utf-8")
    assert "${EnvironmentName}-wwii-*-dlq" in text
    src = (ROOT / "cloudformation" / "compute.yaml").read_text(encoding="utf-8")
    assert "QueueName: !Sub ${EnvironmentName}-wwii-trigger-dlq" in src


def _task_denies():
    res = _resources("iam.yaml")
    stmts = [
        st
        for p in res["ECSTaskRole"]["Properties"]["Policies"]
        for st in p["PolicyDocument"]["Statement"]
    ]
    return {st["Sid"]: st for st in stmts if st.get("Effect") == "Deny"}


def test_a_task_may_not_write_prices_or_stop_reports():
    deny = _task_denies()["NoLaunchStateWrites"]
    keys = deny["Condition"]["ForAnyValue:StringLike"]["dynamodb:LeadingKeys"]
    assert set(keys) == {"price#fargate#*", "stopevt#*"}
    assert "dynamodb:DeleteItem" in deny["Action"]


def test_a_task_may_reset_but_never_climb_a_spot_ladder():
    deny = _task_denies()["NoSpotLadderClimb"]
    assert deny["Condition"]["ForAnyValue:StringLike"]["dynamodb:LeadingKeys"] == [
        "spot#*"
    ]
    assert "dynamodb:DeleteItem" not in deny["Action"]  # spot_ladder.reset on success
    assert {"dynamodb:PutItem", "dynamodb:UpdateItem"} <= set(deny["Action"])
