"""Task 1.1b (owner 2026-11-18): the Fargate Spot price ladder, the daily price refresh, the task
stop marker, and the trigger Lambda's stop report.

Owner rule: finish at the lowest price available at the time, NEVER above the on-demand hourly
price of the task's original size. Rates used here are the real us-east-1 figures checked on
2026-11-18: on-demand $0.04048/vCPU-h and $0.004445/GB-h (Price List API); Spot $0.014542 and
$0.001597 (Cost Explorer, 91.8 vCPU-h and 183.9 GB-h charged 2026-11-04..18).
"""

import json
import os
import time
from datetime import date
from pathlib import Path
from unittest import mock

import boto3
import pytest
import yaml
from moto import mock_aws

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from src.utils import fargate_prices as fp  # noqa: E402
from src.utils import spot_ladder as sl  # noqa: E402
from src.utils import task_marker  # noqa: E402

RATES = fp.Rates(0.014542, 0.001597, 0.04048, 0.004445, int(time.time()))
TABLE = "dev-wwii-api-cache"


@pytest.fixture
def table(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


# --- the ladder --------------------------------------------------------------------------------


def test_phase3_climbs_one_spot_rung_then_goes_on_demand_at_the_original_size():
    """0.5 vCPU / 1 GB: on-demand $0.0247/h. Spot 1/2 is $0.0177 (allowed); Spot 2/4 would be
    $0.0355 (over), so the second reclaim goes on-demand at 0.5/1."""
    assert sl.launch_for(512, 1024, 0, RATES).capacity == sl.SPOT
    assert sl.next_rung(512, 1024, 0, RATES) == 1
    one = sl.launch_for(512, 1024, 1, RATES)
    assert (one.capacity, one.cpu, one.memory) == (sl.SPOT, 1024, 2048)
    assert one.hourly < RATES.hourly(512, 1024, spot=False)
    assert sl.next_rung(512, 1024, 1, RATES) == sl.ONDEMAND
    od = sl.launch_for(512, 1024, sl.ONDEMAND, RATES)
    assert (od.capacity, od.cpu, od.memory) == (sl.FARGATE, 512, 1024)
    assert sl.next_rung(512, 1024, sl.ONDEMAND, RATES) == sl.ONDEMAND


def test_no_launch_ever_costs_more_than_the_original_on_demand_price():
    for base in [(256, 512), (512, 1024), (1024, 2048), (2048, 8192), (4096, 8192)]:
        limit = RATES.hourly(*base, spot=False)
        rung = 0
        for _ in range(8):
            launch = sl.launch_for(*base, rung, RATES)
            assert launch.hourly <= limit + 1e-12, (base, rung)
            rung = sl.next_rung(*base, rung, RATES)


def test_without_rates_spot_is_never_upsized():
    assert sl.launch_for(512, 1024, 0, None).capacity == sl.SPOT
    assert sl.next_rung(512, 1024, 0, None) == sl.ONDEMAND
    assert (
        sl.launch_for(512, 1024, 1, None).capacity == sl.FARGATE
    )  # re-checked at launch


def test_a_rung_that_became_too_expensive_launches_on_demand_original_size():
    pricey = fp.Rates(0.04, 0.0044, 0.04048, 0.004445, int(time.time()))
    launch = sl.launch_for(512, 1024, 1, pricey)
    assert (launch.capacity, launch.cpu, launch.memory) == (sl.FARGATE, 512, 1024)


def test_an_invalid_fargate_size_is_never_requested():
    assert sl.valid_size(512, 1024) and sl.valid_size(16384, 32768)
    assert not sl.valid_size(32768, 65536) and not sl.valid_size(512, 5120)
    assert (
        sl.next_rung(16384, 32768, 0, fp.Rates(0.0001, 0.0001, 1, 1, 0)) == sl.ONDEMAND
    )


@pytest.mark.parametrize(
    "cfg,want",
    [({}, 1.0), ({"spot_ladder": {"max_fraction_of_on_demand": 0.8}}, 0.8),
     ({"spot_ladder": {"max_fraction_of_on_demand": 1.5}}, 1.0),
     ({"spot_ladder": {"max_fraction_of_on_demand": "x"}}, 1.0)],
)  # fmt: skip
def test_the_limit_can_be_tightened_never_loosened(cfg, want):
    assert sl.max_fraction(cfg) == want


def test_a_lower_fraction_stops_the_climb_sooner():
    assert sl.next_rung(512, 1024, 0, RATES, max_fraction=0.5) == sl.ONDEMAND


def test_the_ladder_row_records_reclaims_and_resets_on_success(table):
    fam = "dev-wwii-phase3-enrich"
    assert sl.get_state(table, fam, "Utah") is None
    state = sl.record_reclaim(table, fam, "Utah", 512, 1024, RATES, task_id="t1")
    assert state == {
        "rung": 1,
        "base_cpu": 512,
        "base_mem": 1024,
        "reclaims": 1,
        "recent_tasks": ["t1"],
    }
    # the same stop reported again (a retried report) does not climb twice
    assert (
        sl.record_reclaim(table, fam, "Utah", 512, 1024, RATES, task_id="t1")["rung"]
        == 1
    )
    # the next task ran at rung 1 (1 vCPU / 2 GB); the ORIGINAL size is kept
    state = sl.record_reclaim(table, fam, "Utah", 1024, 2048, RATES, task_id="t2")
    assert state["rung"] == sl.ONDEMAND and state["base_cpu"] == 512
    assert sl.get_state(table, fam, "Utah")["reclaims"] == 2
    sl.reset(table, fam, "Utah")
    assert sl.get_state(table, fam, "Utah") is None


def test_a_ladder_key_cannot_address_another_row():
    with pytest.raises(ValueError):
        sl.state_key("fam", "a#b")
    assert sl.state_key("fam", "") == "spot#fam#_all"


# --- prices ------------------------------------------------------------------------------------


def _ce_response():
    def group(usage_type, cost, usage):
        return {
            "Keys": [usage_type],
            "Metrics": {
                "UnblendedCost": {"Amount": str(cost)},
                "UsageQuantity": {"Amount": str(usage)},
            },
        }

    return {
        "ResultsByTime": [
            {"Groups": [group("USE1-SpotUsage-Fargate-vCPU-Hours:perCPU", 1.0, 50.0),
                        group("USE1-SpotUsage-Fargate-GB-Hours", 0.2, 100.0),
                        group("USE1-Fargate-GB-Hours", 9.0, 1.0)]},  # on-demand: ignored
            {"Groups": [group("USE1-SpotUsage-Fargate-vCPU-Hours:perCPU", 0.5, 50.0)]},
        ]
    }  # fmt: skip


def test_spot_rates_are_what_was_charged():
    ce = mock.Mock()
    ce.get_cost_and_usage.return_value = _ce_response()
    vcpu, gb = fp.spot_rates_from_ce(ce, "us-east-1", date(2026, 11, 18))
    # the HIGHEST daily rate (day 1: 1.0/50), not the 7-day average (0.015)
    assert vcpu == pytest.approx(0.02) and gb == pytest.approx(0.002)
    kw = ce.get_cost_and_usage.call_args.kwargs
    assert kw["TimePeriod"] == {"Start": "2026-11-11", "End": "2026-11-18"}


def test_no_spot_usage_means_no_spot_rate():
    ce = mock.Mock()
    ce.get_cost_and_usage.return_value = {"ResultsByTime": [{"Groups": []}]}
    with pytest.raises(LookupError):
        fp.spot_rates_from_ce(ce, "us-east-1", date(2026, 11, 18))


def _price_list():
    def product(usage_type, price):
        return json.dumps(
            {
                "product": {"attributes": {"usagetype": usage_type}},
                "terms": {"OnDemand": {"t": {"priceDimensions": {"d": {
                    "pricePerUnit": {"USD": str(price)}}}}}},
            }
        )  # fmt: skip

    pricing = mock.Mock()
    pricing.get_paginator.return_value.paginate.return_value = [
        {"PriceList": [product("USE1-Fargate-vCPU-Hours:perCPU", 0.04048),
                       product("USE1-Fargate-GB-Hours", 0.004445),
                       product("USE1-Fargate-ARM-vCPU-Hours:perCPU", 0.03238),
                       product("USE1-SpotUsage-Fargate-GB-Hours", 0.001)]}
    ]  # fmt: skip
    return pricing


def test_on_demand_rates_skip_arm_windows_and_spot():
    assert fp.on_demand_rates(_price_list(), "us-east-1") == (0.04048, 0.004445)


def test_refresh_then_load_and_staleness(table):
    ce = mock.Mock()
    ce.get_cost_and_usage.return_value = _ce_response()
    fp.refresh(ce, _price_list(), table, "us-east-1")
    rates = fp.load(table, "us-east-1")
    assert rates.od_vcpu == 0.04048 and rates.spot_vcpu == pytest.approx(0.02)
    assert fp.load(table, "us-east-1", now=time.time() + 4 * 86400) is None
    assert fp.load(table, "eu-west-1") is None


def test_the_price_refresh_lambda_stores_or_alerts(table, monkeypatch):
    import lambda_handlers.price_refresh as pr

    ce = mock.Mock()
    ce.get_cost_and_usage.return_value = _ce_response()
    assert pr.handler(ce=ce, pricing=_price_list(), table=table) == {"status": "ok"}
    alerts = []
    monkeypatch.setattr(pr, "observe_error", lambda s, e, **k: alerts.append((e, k)))
    ce.get_cost_and_usage.return_value = {"ResultsByTime": []}
    assert pr.handler(ce=ce, pricing=_price_list(), table=table)["status"] == "failed"
    assert (
        alerts[0][0] == "price_refresh_failed"
        and alerts[0][1]["error"] == "LookupError"
    )
    assert fp.load(table, pr.REGION) is not None  # the last good item is kept


# --- the stop marker ---------------------------------------------------------------------------


def test_the_marker_records_and_reads(table):
    task_marker.mark(table, "abc123", claimed=True, book="Utah")
    task_marker.mark(table, "abc123", given_back=True)
    assert task_marker.read(table, "abc123") == {
        "claimed": True,
        "book": "Utah",
        "given_back": True,
    }
    assert task_marker.read(table, "other") == {}


@pytest.mark.parametrize("bad", ["", "a/b", "a#b"])
def test_the_marker_needs_a_task_id(table, bad):
    with pytest.raises(ValueError):
        task_marker.mark(table, bad, claimed=True)


def test_the_marker_refuses_unknown_fields(table):
    with pytest.raises(ValueError):
        task_marker.mark(table, "abc", owner="x")


def test_observe_alert_says_whether_it_was_delivered(monkeypatch):
    from src.utils import observe as obs

    monkeypatch.setattr(obs, "_escalate_to_slack", lambda *a: True)
    assert obs.observe_alert("phase", "x", note="n") is True
    monkeypatch.setattr(obs, "_escalate_to_slack", lambda *a: False)
    assert obs.observe_alert("phase", "y", note="n") is False


# --- the trigger Lambda --------------------------------------------------------------------------


@pytest.fixture
def trigger(table, monkeypatch):
    import lambda_handlers.trigger_handler as th

    monkeypatch.setattr(th, "dynamo", table)
    monkeypatch.setattr(th, "_handle_scheduled_check", lambda: {"action": "checked"})
    monkeypatch.setattr(th, "_trigger_config", lambda: {})
    alerts = []

    def capture(s, e, note="", **k):
        k.pop("dedup_key", None)
        alerts.append((e, note, k))
        return True

    monkeypatch.setattr(th, "observe_error", capture)
    monkeypatch.setattr(th, "observe_alert", capture)
    table.put_item(
        Item={
            "cache_key": fp.item_key(th.AWS_REGION),
            "spot_vcpu": "0.014542",
            "spot_gb": "0.001597",
            "od_vcpu": "0.04048",
            "od_gb": "0.004445",
            "refreshed_at": int(time.time()),
        }
    )
    th.alerts = alerts
    # the module's own family name (another test may have reloaded it with another ENV_NAME)
    th.FAM = th.TASK_FAMILIES[th.PHASE3_TASK_DEF]
    return th


def _stopped(code="SpotInterruption", exit_code=143, book="Utah", cpu="512", mem="1024",
             family=None, task="t1"):  # fmt: skip
    import lambda_handlers.trigger_handler as th

    family = family or th.TASK_FAMILIES[th.PHASE3_TASK_DEF]
    return {
        "source": "aws.ecs",
        "detail-type": "ECS Task State Change",
        "detail": {
            "taskArn": f"arn:aws:ecs:us-east-1:1:task/dev-wwii-pipeline/{task}",
            "group": f"family:{family}",
            "stopCode": code,
            "stoppedReason": "Your Spot Task was interrupted.",
            "cpu": cpu,
            "memory": mem,
            "containers": [{"exitCode": exit_code}],
            "overrides": {
                "containerOverrides": [
                    {"environment": [{"name": "BOOK_NAME", "value": book}]}
                ]
            },
        },
    }


def test_a_spot_reclaim_alerts_moves_the_ladder_and_gives_back(trigger, table):
    from src.utils import pending_queue as pq

    pq.claim_attempt(table, "Utah", 3, task_id="t1")
    task_marker.mark(table, "t1", claimed=True, book="Utah", sigterm=True, synced=True)
    trigger.handler(_stopped(), None)
    [(event, note, fields)] = trigger.alerts
    assert event == "task_reclaimed" and fields["reclaims"] == 1
    assert "Spot 1 vCPU / 2 GB" in note and "limit $0.0247/h" in note
    assert "emergency upload worked" in note and len(note) <= 300
    assert fields["attempt_given_back"] is True
    row = table.get_item(Key={"cache_key": "pending#enrich#Utah"})["Item"]
    assert row["attempts"] == 0
    assert sl.get_state(table, trigger.FAM, "Utah")["rung"] == 1


def test_the_same_stop_from_both_rules_is_reported_once(trigger, table):
    trigger.handler(_stopped(), None)
    trigger.handler(_stopped(), None)
    assert len(trigger.alerts) == 1
    assert sl.get_state(table, trigger.FAM, "Utah")["reclaims"] == 1


def test_a_task_that_gave_back_itself_is_not_forgiven_twice(trigger, table):
    from src.utils import pending_queue as pq

    pq.claim_attempt(table, "Utah", 3, task_id="t0")
    pq.claim_attempt(table, "Utah", 3, task_id="t1")
    pq.give_back(table, "Utah", task_id="t1")  # the task's own SIGTERM handler
    task_marker.mark(table, "t1", claimed=True, given_back=True, sigterm=True)
    trigger.handler(_stopped(), None)
    assert (
        table.get_item(Key={"cache_key": "pending#enrich#Utah"})["Item"]["attempts"]
        == 1
    )


def test_a_task_that_never_claimed_gives_nothing_back(trigger, table):
    from src.utils import pending_queue as pq

    pq.claim_attempt(table, "Utah", 3)  # an earlier run's real failure
    trigger.handler(_stopped(), None)  # no marker at all: killed before its claim
    assert (
        table.get_item(Key={"cache_key": "pending#enrich#Utah"})["Item"]["attempts"]
        == 1
    )
    assert "killed before its emergency upload" in trigger.alerts[0][1]


def test_the_lambda_never_gives_back_another_runs_attempt(trigger, table):
    """The marker says t1 claimed, but the row's claim is a newer task's (it relaunched)."""
    from src.utils import pending_queue as pq

    pq.claim_attempt(table, "Utah", 3, task_id="t1")
    pq.claim_attempt(table, "Utah", 3, task_id="t2")
    task_marker.mark(table, "t1", claimed=True, sigterm=True)
    trigger.handler(_stopped(), None)
    assert (
        table.get_item(Key={"cache_key": "pending#enrich#Utah"})["Item"]["attempts"]
        == 2
    )


def test_an_alert_the_task_could_not_send_is_relayed(trigger, table):
    """No NAT: the task left its own alert on the marker; ECS reports the stop (exit 1)."""
    note = "NAT never became ready after 3 attempts, so the phase stopped. " + "x" * 300
    task_marker.mark(
        table,
        "t1",
        unsent_event="nat_not_ready",
        unsent_note=note[: task_marker.UNSENT_NOTE_MAX],
        book="Utah",
    )
    trigger.handler(_stopped(code="EssentialContainerExited", exit_code=1), None)
    [(event, sent, fields)] = trigger.alerts
    assert event == "task_alert_relayed" and fields["task_alert"] == "nat_not_ready"
    assert "NAT never became ready" in sent and len(sent) <= 300
    assert "killed before its emergency upload" in sent  # the trigger's own facts stay


def test_a_relayed_alert_carries_no_slack_markup(trigger, table):
    task_marker.mark(
        table,
        "t1",
        unsent_event="x<!channel>",
        unsent_note="go <https://evil|here> @all",
    )
    trigger.handler(_stopped(code="EssentialContainerExited", exit_code=1), None)
    [(_, sent, fields)] = trigger.alerts
    assert not set("<>@") & set(sent + fields["task_alert"])


def test_a_relayed_alert_is_not_sent_when_the_task_delivered_one(trigger, table):
    task_marker.mark(
        table, "t1", unsent_event="nat_not_ready", unsent_note="x", alerted=True
    )
    trigger.handler(_stopped(code="EssentialContainerExited", exit_code=1), None)
    assert trigger.alerts == []


def test_two_reclaims_together_are_two_alerts(trigger, table, monkeypatch):
    from src.utils import observe as obs

    sent = []
    monkeypatch.setattr(trigger, "observe_alert", obs.observe_alert)
    monkeypatch.setattr(obs, "_slack_enabled", lambda: True)
    monkeypatch.setattr(
        obs, "publish_alert", lambda subj, line: sent.append(line) or True
    )
    trigger.handler(_stopped(task="a1", book="Utah"), None)
    trigger.handler(_stopped(task="a2", book="Omaha"), None)
    assert len(sent) == 2


def test_a_report_that_never_finished_is_retried_without_climbing_twice(trigger, table):
    trigger.handler(_stopped(), None)
    table.update_item(  # as if that Lambda timed out before marking the report done
        Key={"cache_key": "stopevt#t1"},
        UpdateExpression="SET #s = :s, started_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "started", ":t": int(time.time()) - 2000},
    )
    trigger.handler(_stopped(), None)
    assert len(trigger.alerts) == 2  # reported again
    assert sl.get_state(table, trigger.FAM, "Utah")["rung"] == 1  # but climbed once
    trigger.handler(_stopped(), None)
    assert len(trigger.alerts) == 2  # done now


def test_an_upsized_task_carries_its_original_size(trigger, table):
    """The ladder row is gone (expired) while an upsized task ran: the limit stays the original
    size's, from SPOT_LADDER_BASE."""
    ev = _stopped(cpu="1024", mem="2048")
    ev["detail"]["overrides"]["containerOverrides"][0]["environment"].append(
        {"name": "SPOT_LADDER_BASE", "value": "512x1024"}
    )
    trigger.handler(ev, None)
    state = sl.get_state(table, trigger.FAM, "Utah")
    assert (state["base_cpu"], state["base_mem"]) == (512, 1024)


def test_the_worst_case_alert_notes_fit(trigger, table, monkeypatch):
    monkeypatch.setattr(trigger, "_ladder_inputs", lambda: (None, 1.0))
    table.put_item(
        Item={"cache_key": f"spot#{trigger.FAM}#Utah", "rung": 0, "base_cpu": 512,
              "base_mem": 1024, "reclaims": 98}
    )  # fmt: skip
    trigger.handler(_stopped(), None)
    ev = _stopped(code="EssentialContainerExited", exit_code=137, task="t2")
    ev["detail"]["stoppedReason"] = "x" * 300
    trigger.handler(ev, None)
    assert all(len(note) <= 300 for _, note, _ in trigger.alerts), trigger.alerts


def test_a_killed_task_that_reported_nothing_is_alerted(trigger):
    trigger.handler(_stopped(code="EssentialContainerExited", exit_code=137), None)
    [(event, note, _)] = trigger.alerts
    assert event == "task_stopped_unreported" and "exit 137" in note


def test_a_stop_the_task_already_alerted_is_not_alerted_again(trigger, table):
    task_marker.mark(table, "t1", alerted=True)
    trigger.handler(_stopped(code="EssentialContainerExited", exit_code=1), None)
    assert trigger.alerts == []


def test_an_ecs_stop_the_task_recorded_is_alerted_with_its_cause(trigger, table):
    task_marker.mark(table, "t1", sigterm=True, synced=False)
    trigger.handler(_stopped(code="UserInitiated"), None)
    [(event, note, _)] = trigger.alerts
    assert event == "task_stopped" and "UserInitiated" in note and "FAILED" in note


def test_a_task_that_failed_to_start_is_alerted(trigger):
    ev = _stopped(code="TaskFailedToStart")
    ev["detail"]["containers"] = [{}]
    trigger.handler(ev, None)
    assert trigger.alerts[0][0] == "task_stopped_unreported"
    note = trigger.alerts[0][1]
    assert "never started" in note and "exit" not in note


def test_services_are_not_reported(trigger):
    trigger.handler(_stopped(family="dev-wwii-openserp"), None)
    assert trigger.alerts == []


def test_a_report_failure_still_runs_the_recovery(trigger, monkeypatch):
    monkeypatch.setattr(
        trigger, "_report_task_stop", mock.Mock(side_effect=RuntimeError("x"))
    )
    assert trigger.handler(_stopped(), None) == {"action": "checked"}
    assert trigger.alerts[0][0] == "stop_report_failed"


# --- launching on the ladder ---------------------------------------------------------------------


class _Calls(list):
    """RunTask calls; ``no_spot`` makes Spot place nothing."""

    no_spot = False


@pytest.fixture
def launches(trigger, monkeypatch):
    calls = _Calls()

    def run_task(**kw):
        calls.append(kw)
        if (
            kw["capacityProviderStrategy"][0]["capacityProvider"] == sl.SPOT
            and calls.no_spot
        ):
            return {"tasks": [], "failures": [{"reason": "Capacity is unavailable"}]}
        return {"tasks": [{"taskArn": "x"}], "failures": []}

    monkeypatch.setattr(trigger.ecs, "run_task", run_task)
    monkeypatch.setattr(trigger, "_notify_launch", lambda *a: None)
    return calls


def _launch(trigger):
    trigger._launch_ecs_task(
        trigger.PHASE3_TASK_DEF,
        "test",
        "Utah",
        None,
        "lock#x",
        trigger.FAM,
    )


def test_a_first_launch_is_spot_at_the_task_definition_size(trigger, launches):
    _launch(trigger)
    kw = launches[0]
    assert kw["capacityProviderStrategy"] == [
        {"capacityProvider": sl.SPOT, "weight": 1, "base": 0}
    ]
    assert "cpu" not in kw["overrides"] and "memory" not in kw["overrides"]


def test_after_a_reclaim_the_next_launch_is_the_next_rung(trigger, launches, table):
    sl.record_reclaim(table, trigger.FAM, "Utah", 512, 1024, RATES)
    _launch(trigger)
    kw = launches[0]
    assert kw["capacityProviderStrategy"][0]["capacityProvider"] == sl.SPOT
    assert (kw["overrides"]["cpu"], kw["overrides"]["memory"]) == ("1024", "2048")
    env = kw["overrides"]["containerOverrides"][0]["environment"]
    assert {"name": "SPOT_LADDER_BASE", "value": "512x1024"} in env
    assert {"name": "BOOK_NAME", "value": "Utah"} in env


def test_at_the_top_the_launch_is_on_demand_at_the_original_size(
    trigger, launches, table
):
    for size in ((512, 1024), (1024, 2048)):
        sl.record_reclaim(table, trigger.FAM, "Utah", *size, RATES)
    _launch(trigger)
    kw = launches[0]
    assert kw["capacityProviderStrategy"][0]["capacityProvider"] == sl.FARGATE
    assert "cpu" not in kw["overrides"]


def test_no_spot_capacity_falls_back_to_on_demand_original_size(
    trigger, launches, table
):
    sl.record_reclaim(table, trigger.FAM, "Utah", 512, 1024, RATES)
    launches.no_spot = True
    _launch(trigger)
    assert [k["capacityProviderStrategy"][0]["capacityProvider"] for k in launches] == [
        sl.SPOT,
        sl.FARGATE,
    ]
    assert "cpu" not in launches[1]["overrides"]  # never a larger on-demand size


def test_a_launch_error_that_may_have_started_a_task_is_not_retried(
    trigger, launches, monkeypatch
):
    def throttled(**kw):
        launches.append(kw)
        raise RuntimeError("ThrottlingException / read timeout")

    monkeypatch.setattr(trigger.ecs, "run_task", throttled)
    with pytest.raises(RuntimeError):
        _launch(trigger)
    assert len(launches) == 1  # no on-demand second task


def test_stale_rates_never_upsize(trigger, launches, table):
    sl.record_reclaim(table, trigger.FAM, "Utah", 512, 1024, RATES)
    table.update_item(
        Key={"cache_key": fp.item_key(trigger.AWS_REGION)},
        UpdateExpression="SET refreshed_at = :t",
        ExpressionAttributeValues={":t": int(time.time()) - 5 * 86400},
    )
    _launch(trigger)
    assert launches[0]["capacityProviderStrategy"][0]["capacityProvider"] == sl.FARGATE
    assert "cpu" not in launches[0]["overrides"]


# --- the ECS wrapper -------------------------------------------------------------------------------


@pytest.fixture
def task(table, monkeypatch):
    import ecs_entrypoint as ee

    monkeypatch.setattr(ee, "_cache_table", lambda: table)
    monkeypatch.setenv("ECS_TASK_ID", "t9")
    monkeypatch.setenv("ECS_TASK_FAMILY", "dev-wwii-phase3-enrich")
    monkeypatch.setenv("BOOK_NAME", "Utah")
    monkeypatch.setattr(ee, "_current_phase_script", "phase3_enrich_data.py")
    for name in ("_stop_alerted", "_watchdog_stop", "_attempt_claimed"):
        monkeypatch.setattr(ee, name, False)
    for name in (
        "_final_sync",
        "_remove_lock",
        "_release_nat_lease",
        "_requeue_interrupted_resume",
    ):
        monkeypatch.setattr(ee, name, lambda *a, **k: None)
    return ee


def test_an_outside_sigterm_leaves_the_alert_to_the_trigger(task, table, monkeypatch):
    sent = []
    monkeypatch.setattr(task, "_alert_phase_stopped", lambda *a, **k: sent.append(a))
    with pytest.raises(SystemExit):
        task._handle_sigterm(15, None)
    assert sent == []  # one alert, from the trigger, with the cause
    assert task_marker.read(table, "t9") == {"sigterm": True, "synced": True}


def test_without_a_marker_the_task_alerts_itself(task, monkeypatch):
    sent = []
    monkeypatch.setattr(task, "_alert_phase_stopped", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(task, "_mark_task", lambda **k: False)
    with pytest.raises(SystemExit):
        task._handle_sigterm(15, None)
    assert sent and "relaunched when ECS reports the stop" in sent[0][1]


def test_a_claim_and_its_give_back_are_recorded(task, table, monkeypatch):
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    assert task._claim_enrich_attempt() is True
    assert task_marker.read(table, "t9") == {"claimed": True, "book": "Utah"}
    task._give_back_own_attempt()
    assert task_marker.read(table, "t9")["given_back"] is True


def test_success_resets_the_ladder(task, table):
    sl.record_reclaim(table, "dev-wwii-phase3-enrich", "Utah", 512, 1024, RATES)
    task._reset_spot_ladder()  # ECS_TASK_FAMILY=dev-wwii-phase3-enrich (fixture)
    assert sl.get_state(table, "dev-wwii-phase3-enrich", "Utah") is None


def test_an_alert_that_was_delivered_is_recorded(task, table, monkeypatch):
    monkeypatch.setattr(task, "observe_alert", lambda *a, **k: True)
    task._alert_phase_stopped("x", "note")
    assert task_marker.read(table, "t9") == {"alerted": True}


def test_an_alert_that_was_not_delivered_is_left_to_the_trigger(
    task, table, monkeypatch
):
    monkeypatch.setattr(task, "observe_alert", lambda *a, **k: False)
    task._alert_phase_stopped("x", "note")
    # not marked alerted: the trigger alerts this stop, relaying the task's own text
    assert task_marker.read(table, "t9") == {"unsent_event": "x", "unsent_note": "note"}


# --- infrastructure --------------------------------------------------------------------------------


class _CfnLoader(yaml.SafeLoader):
    pass


_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _compute():
    path = Path(__file__).resolve().parent.parent / "cloudformation" / "compute.yaml"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_CfnLoader)["Resources"]


def test_the_task_failure_rule_passes_the_event_and_catches_failed_starts():
    rule = _compute()["TaskFailureRule"]["Properties"]
    assert "Input" not in rule["Targets"][0]
    either = rule["EventPattern"]["detail"]["$or"]
    assert {"stopCode": ["TaskFailedToStart"]} in either


def test_the_price_refresh_role_is_least_privilege():
    res = _compute()
    stmts = res["PriceRefreshRole"]["Properties"]["Policies"][0]["PolicyDocument"][
        "Statement"
    ]
    actions = sorted(
        a
        for s in stmts
        for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])
    )
    assert actions == [
        "ce:GetCostAndUsage",
        "dynamodb:PutItem",
        "pricing:GetProducts",
        "sns:Publish",
    ]
    ddb = next(s for s in stmts if s["Action"] == "dynamodb:PutItem")
    assert ddb["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"] == [
        "price#fargate#*"
    ]
    assert (
        res["PriceRefreshSchedule"]["Properties"]["ScheduleExpression"] == "rate(1 day)"
    )


def test_snapshot_restore_keeps_prices_and_resets_ladder_state():
    from scripts import phase_snapshot as ps

    assert ps.classify("price#fargate#us-east-1") == "ignore"
    for key in ("spot#f#Utah", "taskstop#t1", "stopevt#t1"):
        assert ps.classify(key) == "reset"


@pytest.mark.parametrize(
    "rates",
    [fp.Rates(0.0, 0.001597, 0.04048, 0.004445, 0),  # Spot rounded to zero
     fp.Rates(0.001, 0.001597, 0.04048, 0.004445, 0),  # implausibly cheap
     fp.Rates(0.05, 0.001597, 0.04048, 0.004445, 0),  # above on-demand
     fp.Rates(0.014542, 0.001597, float("inf"), 0.004445, 0),
     fp.Rates(0.014542, float("nan"), 0.04048, 0.004445, 0)],
)  # fmt: skip
def test_implausible_rates_are_refused(rates):
    assert not rates.sane()
    assert RATES.sane()


def test_an_implausible_refresh_keeps_the_last_good_item(table):
    ce = mock.Mock()
    ce.get_cost_and_usage.return_value = _ce_response()
    fp.refresh(ce, _price_list(), table, "us-east-1")
    ce.get_cost_and_usage.return_value = {
        "ResultsByTime": [{"Groups": [
            {"Keys": ["USE1-SpotUsage-Fargate-vCPU-Hours:perCPU"],
             "Metrics": {"UnblendedCost": {"Amount": "0.0000001"}, "UsageQuantity": {"Amount": "5"}}},
            {"Keys": ["USE1-SpotUsage-Fargate-GB-Hours"],
             "Metrics": {"UnblendedCost": {"Amount": "0.01"}, "UsageQuantity": {"Amount": "5"}}}]}]
    }  # fmt: skip
    with pytest.raises(ValueError):
        fp.refresh(ce, _price_list(), table, "us-east-1")
    assert fp.load(table, "us-east-1").spot_vcpu == pytest.approx(0.02)


def test_a_tampered_stored_item_is_treated_as_no_rates(table):
    table.put_item(
        Item={"cache_key": fp.item_key("us-east-1"), "spot_vcpu": "0", "spot_gb": "0",
              "od_vcpu": "0.04048", "od_gb": "0.004445", "refreshed_at": int(time.time())}
    )  # fmt: skip
    assert fp.load(table, "us-east-1") is None


def test_cost_explorer_pages_are_all_read_and_tiny_days_ignored():
    def group(t, cost, usage):
        return {"Keys": [t], "Metrics": {"UnblendedCost": {"Amount": str(cost)},
                                          "UsageQuantity": {"Amount": str(usage)}}}  # fmt: skip

    ce = mock.Mock()
    ce.get_cost_and_usage.side_effect = [
        {"ResultsByTime": [{"Groups": [group("X-SpotUsage-Fargate-vCPU-Hours:perCPU", 0.01, 0.1)]}],
         "NextPageToken": "p2"},
        {"ResultsByTime": [{"Groups": [group("X-SpotUsage-Fargate-vCPU-Hours:perCPU", 0.7, 50),
                                       group("X-SpotUsage-Fargate-GB-Hours", 0.1, 50)]}]},
    ]  # fmt: skip
    vcpu, gb = fp.spot_rates_from_ce(ce, "us-east-1", date(2026, 11, 18))
    assert vcpu == pytest.approx(0.014) and gb == pytest.approx(
        0.002
    )  # 0.1 h day ignored
    assert ce.get_cost_and_usage.call_args_list[1].kwargs["NextPageToken"] == "p2"


def test_a_suppressed_alert_is_not_delivered(monkeypatch):
    from src.utils import observe as obs

    monkeypatch.setattr(obs, "_slack_enabled", lambda: True)
    monkeypatch.setattr(obs, "publish_alert", lambda *a: True)
    monkeypatch.setattr(obs, "_slack_last_sent", {})
    assert obs.observe_alert("phase", "z", note="n", dedup_key="t1") is True
    assert obs.observe_alert("phase", "z", note="n", dedup_key="t1") is False
    assert obs.observe_alert("phase", "z", note="n", dedup_key="t2") is True


def test_an_undelivered_stop_alert_fails_the_invocation_and_is_retried(
    trigger, table, monkeypatch
):
    from src.utils import pending_queue as pq

    calls = []
    pq.claim_attempt(table, "Utah", 3, task_id="t0")
    pq.claim_attempt(table, "Utah", 3, task_id="t1")
    task_marker.mark(table, "t1", claimed=True, sigterm=True)

    def status():
        return table.get_item(Key={"cache_key": "stopevt#t1"})["Item"]["status"]

    monkeypatch.setattr(
        trigger, "observe_alert", lambda *a, **k: (calls.append(a[1]), False)[1]
    )
    # handled, not raised: the report is stored for the next scheduled check (no Lambda error
    # alarm or DLQ message for a stop that is already taken care of)
    assert trigger.handler(_stopped(), None) == {"action": "checked"}
    assert status() == "failed"
    # another reclaim of the same book is recorded before the retry
    monkeypatch.setattr(
        trigger, "observe_alert", lambda *a, **k: (calls.append(a[1]), True)[1]
    )
    trigger.handler(_stopped(task="t2", cpu="1024", mem="2048"), None)
    assert trigger.handler(_stopped(), None) == {"action": "checked"}  # the retry of t1
    assert status() == "done"
    assert calls == ["task_reclaimed"] * 3
    state = sl.get_state(table, trigger.FAM, "Utah")
    assert state["reclaims"] == 2 and state["rung"] == sl.ONDEMAND  # t1 counted once
    attempts = table.get_item(Key={"cache_key": "pending#enrich#Utah"})["Item"][
        "attempts"
    ]
    assert attempts == 1  # t1's attempt given back once, t0's kept


def test_a_carried_base_larger_than_the_task_is_ignored(table):
    state = sl.record_reclaim(
        table, "fam", "Utah", 512, 1024, RATES, task_id="t1", base=(4096, 8192)
    )
    assert (state["base_cpu"], state["base_mem"]) == (512, 1024)


def test_an_unknown_task_size_is_refused(table):
    with pytest.raises(ValueError):
        sl.record_reclaim(table, "fam", "Utah", 0, 0, RATES, task_id="t1")
    assert sl.get_state(table, "fam", "Utah") is None


def test_a_failed_publish_does_not_suppress_its_retry(monkeypatch):
    from src.utils import observe as obs

    monkeypatch.setattr(obs, "_slack_enabled", lambda: True)
    monkeypatch.setattr(obs, "_slack_last_sent", {})
    monkeypatch.setattr(obs, "publish_alert", lambda *a: False)
    assert obs.observe_alert("phase", "r", note="n", dedup_key="t1") is False
    monkeypatch.setattr(obs, "publish_alert", lambda *a: True)
    assert obs.observe_alert("phase", "r", note="n", dedup_key="t1") is True


def test_a_bad_size_is_alerted_and_recovery_still_runs(trigger):
    assert trigger.handler(_stopped(cpu="0", mem="0"), None) == {"action": "checked"}
    assert trigger.alerts[0][0] == "stop_report_failed"


def test_an_sns_outage_does_not_make_every_error_wait_on_a_publish(monkeypatch):
    from src.utils import observe as obs

    tries = []
    monkeypatch.setattr(obs, "_slack_enabled", lambda: True)
    monkeypatch.setattr(obs, "_slack_last_sent", {})
    monkeypatch.setattr(obs, "publish_alert", lambda *a: (tries.append(1), False)[1])
    for _ in range(5):
        obs.observe_error("phase", "loop", note="n")
    assert len(tries) == 1
