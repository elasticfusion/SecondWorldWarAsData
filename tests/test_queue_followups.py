"""Group 1 follow-ups (2026-11-19): repeating reminders share one alert window; a held book is
re-announced once a day; runnable books are ordered so one book renewing the cluster-wide hold
cannot starve the rest; a step failure records its steps on the queue row and says which attempt
this was; the older preflight stops free the lock at once; the end-of-run chain asks the trigger
to try every queued book. Run against moto, so the condition expressions are really parsed.
"""

import json
import os
from unittest import mock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as ee  # noqa: E402
from scripts import resume_book  # noqa: E402
from src.utils import alert_window, stop_cause  # noqa: E402
from src.utils import pending_queue as pq  # noqa: E402

TABLE = "dev-wwii-api-cache"


@pytest.fixture
def table(monkeypatch):
    for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        yield ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


@pytest.fixture
def phase3(table, monkeypatch):
    for name in ("_stop_alerted", "_watchdog_stop", "_attempt_claimed"):
        monkeypatch.setattr(ee, name, False)
    monkeypatch.setattr(ee, "_cache_table", lambda: table)
    monkeypatch.setattr(ee, "_current_phase_script", "phase3_enrich_data.py")
    monkeypatch.setenv("BOOK_NAME", "Utah")
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    return table


def _row(table, book):
    return table.get_item(Key={"cache_key": f"pending#enrich#{book}"}).get("Item")


# --- alert_window ----------------------------------------------------------------------------


def test_the_alert_window_lets_one_alert_through_per_window(table):
    assert alert_window.claim(table, "k", 3600, now=1000.0)
    assert not alert_window.claim(table, "k", 3600, now=2000.0)
    assert alert_window.claim(table, "k", 3600, now=1000.0 + 3601)
    assert alert_window.claim(table, "other", 3600, now=2000.0)  # one window per key


@pytest.mark.parametrize(
    "marker",
    [{"at": 10**10}, {}, {"at": "soon"}],
    ids=["future", "no-time", "not-a-number"],
)
def test_a_tampered_marker_does_not_silence_the_alert(table, marker):
    table.put_item(Item={"cache_key": "k", **marker})
    assert alert_window.claim(table, "k", 3600, now=1000.0)


def test_a_released_claim_is_sent_by_the_next_caller(table, caplog):
    assert alert_window.claim(table, "k", 3600, now=1000.0)
    alert_window.release(table, "k")
    assert alert_window.claim(table, "k", 3600, now=1001.0)
    broken = mock.Mock()
    broken.delete_item.side_effect = RuntimeError("ddb")
    alert_window.release(broken, "k")  # never raises
    assert "OBSERV alert_window.release_failed" in caplog.text


def test_a_dynamodb_error_alerts_anyway(caplog):
    broken = mock.Mock()
    broken.put_item.side_effect = RuntimeError("ProvisionedThroughputExceeded")
    assert alert_window.claim(broken, "k", 60)
    assert "OBSERV alert_window.marker_failed" in caplog.text


# --- queue order and the held-book reminder --------------------------------------------------


def test_never_deferred_books_go_first_then_the_oldest_deferral(table):
    for book in ("Anzio", "Brest", "Caen", "Dieppe"):
        pq.queue_enrich(table, book)
    pq.defer(table, "Anzio", "connectivity", "x")  # the newest deferral...
    table.update_item(
        Key={"cache_key": "pending#enrich#Brest"},
        UpdateExpression="SET defer_cause = :c, deferred_at = :t",
        ExpressionAttributeValues={":c": "funding", ":t": 100},  # ...and an old one
    )
    later = 10**10  # past every retry interval
    # was alphabetical: Anzio, renewing the hold on every retry, would always go first
    assert pq.runnable_books(table, "enrich", now=later) == [
        "Caen",
        "Dieppe",
        "Brest",
        "Anzio",
    ]


def test_held_books_are_reminded_once_a_day(table, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "src.utils.observe.observe_alert",
        lambda s, e, **k: sent.append((e, k)) or True,
    )
    pq.queue_enrich(table, "Utah")
    pq.defer(table, "Utah", "failure", "Phase 3 failed 3 times")
    assert pq.runnable_books(table, "enrich", now=1000.0) == []
    pq.runnable_books(table, "enrich", now=2000.0)  # the next hourly reconcile: silent
    pq.runnable_books(table, "enrich", now=1000.0 + pq.HELD_REMINDER_HOURS * 3600 + 1)
    assert [e for e, _ in sent] == ["held_reminder", "held_reminder"]
    note = sent[0][1]["note"]
    assert "Utah" in note and pq.RESUME_COMMAND in note and sent[0][1]["count"] == 1


def test_a_long_held_list_is_shortened_in_the_reminder(table, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "src.utils.observe.observe_alert", lambda s, e, **k: sent.append(k) or True
    )
    books = [f"Book{i:02d}" for i in range(13)]
    assert pq.remind_held(table, "enrich", books, now=1000.0)
    assert "(+3 more)" in sent[0]["note"] and "Book10" not in sent[0]["note"]


def test_an_undelivered_reminder_is_retried_by_the_next_reconcile(table, monkeypatch):
    delivered = iter([False, True])
    monkeypatch.setattr(
        "src.utils.observe.observe_alert", lambda s, e, **k: next(delivered)
    )
    assert pq.remind_held(table, "enrich", ["Utah"], now=1000.0) is False
    assert pq.remind_held(table, "enrich", ["Utah"], now=1060.0) is True


def test_a_reminder_failure_never_stops_the_reconcile(table, monkeypatch, caplog):
    monkeypatch.setattr(
        "src.utils.observe.observe_alert", mock.Mock(side_effect=RuntimeError("sns"))
    )
    assert pq.remind_held(table, "enrich", ["Utah"]) is False
    assert "OBSERV queue.held_reminder_failed" in caplog.text


# --- failed steps on the queue row -----------------------------------------------------------


def test_failed_steps_are_kept_listed_and_cleared_by_the_next_start(table, capsys):
    pq.queue_enrich(table, "Utah")
    pq.record_failed_steps(table, "Utah", ["geocode", "weather"])
    assert pq.failed_steps(table, "Utah") == ["geocode", "weather"]
    pq.defer(table, "Utah", "failure", "Phase 3 failed 3 times")
    assert pq.deferred_rows(table)[0]["last_failed_steps"] == ["geocode", "weather"]
    assert resume_book.main(["list"], table=table) == 0
    assert "failed_steps=geocode,weather" in capsys.readouterr().out
    pq.claim_attempt(table, "Utah", 5)  # an allowed start: this run's steps are its own
    assert pq.failed_steps(table, "Utah") == []


def test_a_refused_start_keeps_the_steps_for_its_hold_alert(phase3, monkeypatch):
    sent = []
    monkeypatch.setattr(ee, "observe_error", lambda s, e, **k: sent.append((e, k)))
    pq.queue_enrich(phase3, "Utah")
    for _ in range(pq.DEFAULT_MAX_ATTEMPTS):
        pq.claim_attempt(phase3, "Utah", pq.DEFAULT_MAX_ATTEMPTS)
    steps = ["openserp_people", "geocode", "weather", "images", "awards", "units"]
    pq.record_failed_steps(phase3, "Utah", steps)
    assert ee._claim_enrich_attempt() is False
    [(event, fields)] = sent
    assert event == "enrich_deferred"
    assert fields["failed_steps"] == "openserp_people, geocode, weather, images (+2)"
    assert fields["note"].endswith("Last failed step(s): " + fields["failed_steps"])


def test_failed_steps_are_never_written_to_a_missing_row(table):
    with pytest.raises(Exception, match="ConditionalCheckFailed"):
        pq.record_failed_steps(table, "Ghost", ["geocode"])
    assert _row(table, "Ghost") is None


def test_a_step_failure_records_its_steps_and_says_which_attempt(phase3, monkeypatch):
    monkeypatch.setattr(
        ee, "_phase_results", lambda: {"errored_sources": ["geocode", "weather"]}
    )
    assert ee._claim_enrich_attempt() is True
    note = ee._defer_on_stop_cause(stop_cause.EXIT_STEP_FAILED)
    assert f"attempt 1 of {pq.DEFAULT_MAX_ATTEMPTS}" in note and "stays queued" in note
    assert note.endswith("Failed step(s): geocode, weather")
    assert pq.failed_steps(phase3, "Utah") == ["geocode", "weather"]


def test_a_step_with_requests_set_aside_is_named_and_recorded(phase3, monkeypatch):
    """Batch rounds set a stuck request aside: the step is unfinished (not "errored"), but the
    failure alert and the queue row must still name it, or the operator is told "unknown".
    """
    monkeypatch.setattr(
        ee,
        "_phase_results",
        lambda: {"errored_sources": ["geocode"], "pending_sources": ["places"]},
    )
    assert ee._claim_enrich_attempt() is True
    note = ee._defer_on_stop_cause(stop_cause.EXIT_STEP_FAILED)
    assert note.endswith("Failed step(s): geocode, places") and "unknown" not in note
    assert pq.failed_steps(phase3, "Utah") == ["geocode", "places"]


def test_the_last_attempt_says_the_book_will_be_held(phase3, monkeypatch):
    many = [f"step_with_a_long_name_{i}" for i in range(9)]
    monkeypatch.setattr(ee, "_phase_results", lambda: {"errored_sources": many})
    pq.queue_enrich(phase3, "Utah")
    for _ in range(pq.DEFAULT_MAX_ATTEMPTS - 1):
        pq.claim_attempt(phase3, "Utah", pq.DEFAULT_MAX_ATTEMPTS)
    assert ee._claim_enrich_attempt() is True
    note = ee._defer_on_stop_cause(stop_cause.EXIT_STEP_FAILED)
    cap = pq.DEFAULT_MAX_ATTEMPTS
    assert note.startswith(
        f"Phase 3 did not finish: this was the LAST attempt ({cap} of {cap})"
    )
    # what to do comes first and survives observe's 300-character note cap
    assert note.index(f"{pq.RESUME_COMMAND} Utah") < 300 and "(+5)" in note


def test_a_step_failure_whose_steps_cannot_be_stored_still_alerts(
    phase3, monkeypatch, caplog
):
    monkeypatch.setattr(ee, "_phase_results", lambda: {"errored_sources": ["geocode"]})
    monkeypatch.setattr(
        pq, "record_failed_steps", mock.Mock(side_effect=RuntimeError("ddb"))
    )
    note = ee._defer_on_stop_cause(stop_cause.EXIT_STEP_FAILED)
    assert "attempt-capped" in note and "geocode" in note  # no claim: no attempt number
    assert "OBSERV queue.failed_steps_not_recorded" in caplog.text


# --- the older preflight stops free the lock at once (D10) -----------------------------------


@pytest.mark.parametrize(
    "status,text,event",
    [
        (402, "", "grok_credits_depleted"),
        (403, "no credit left", "grok_credits_depleted"),
        (401, "", "grok_key_invalid"),
    ],
)
def test_a_grok_preflight_stop_frees_the_lock_and_nat(
    phase3, monkeypatch, status, text, event
):
    sent, freed = [], []
    monkeypatch.setattr(ee, "_alert_phase_stopped", lambda e, n, **k: sent.append(e))
    monkeypatch.setattr(ee, "_remove_lock", lambda p: freed.append("lock"))
    monkeypatch.setattr(ee, "_release_nat_lease", lambda: freed.append("nat"))
    monkeypatch.setenv("GROK_API_KEY", "k")
    resp = mock.Mock(status_code=status, text=text)
    monkeypatch.setattr("requests.post", lambda *a, **k: resp)
    with pytest.raises(SystemExit) as stop:
        ee._preflight_credit_check()
    assert stop.value.code == 1 and sent == [event] and freed == ["lock", "nat"]


def test_a_model_preflight_stop_frees_the_lock(phase3, monkeypatch):
    from src.utils import grok_models

    freed = []
    monkeypatch.setattr(ee, "_alert_phase_stopped", lambda e, n, **k: None)
    monkeypatch.setattr(ee, "_remove_lock", lambda p: freed.append("lock"))
    monkeypatch.setattr(ee, "_release_nat_lease", lambda: freed.append("nat"))
    monkeypatch.setenv("GROK_API_KEY", "k")
    monkeypatch.setattr(
        "requests.post",
        mock.Mock(side_effect=grok_models.GrokModelUnavailable("grok-x")),
    )
    with pytest.raises(SystemExit):
        ee._preflight_credit_check()
    assert freed == ["lock", "nat"]


# --- the end-of-run chain --------------------------------------------------------------------


def test_the_chain_asks_the_trigger_to_try_every_queued_book(monkeypatch):
    client = mock.Mock()
    monkeypatch.setattr(ee.boto3, "client", lambda *a, **k: client)
    monkeypatch.setenv("ENV_NAME", "dev")
    assert ee._invoke_trigger({"source": "enrich-next"}, "next book")
    kw = client.invoke.call_args.kwargs
    assert kw["FunctionName"] == "dev-wwii-trigger" and kw["InvocationType"] == "Event"
    assert json.loads(kw["Payload"]) == {"source": "enrich-next"}


def test_a_lost_trigger_invoke_warns_and_names_the_backstop(monkeypatch, caplog):
    monkeypatch.setattr(
        ee.boto3, "client", mock.Mock(side_effect=RuntimeError("throttled"))
    )
    assert not ee._invoke_trigger({"source": "phase-complete", "phase": "2"}, "x")
    assert "OBSERV trigger.invoke_failed" in caplog.text and "hourly" in caplog.text


def test_the_trigger_routes_enrich_next_to_the_phase3_drain(monkeypatch):
    import lambda_handlers.trigger_handler as th

    seen = []
    monkeypatch.setattr(th, "_reconcile_phase3", lambda r: seen.append(r) or True)
    assert th.handler({"source": "enrich-next"}, None) == {"queued_work": True}
    assert seen == ["enrich-next"]


def test_the_phase3_drain_tries_the_next_book_when_the_first_is_running(monkeypatch):
    import lambda_handlers.trigger_handler as th

    tried = []
    monkeypatch.setattr(th, "_get_pending_books_for_enrich", lambda: ["Anzio", "Brest"])
    monkeypatch.setattr(
        th,
        "_run_task",
        lambda td, why, book_name: tried.append(book_name) or book_name == "Brest",
    )
    assert th._reconcile_phase3("enrich-next") is True
    assert tried == ["Anzio", "Brest"]


# --- a counted failure is never given back (1.2b follow-up) ----------------------------------


def test_a_failed_phase_keeps_its_attempt_through_a_later_sigterm(phase3, monkeypatch):
    marks = []
    monkeypatch.setattr(ee, "_mark_task", lambda **f: marks.append(f) or True)
    assert ee._claim_enrich_attempt() is True
    ee._attempt_counts()  # the phase failed (exit 1); its alert may not have been delivered
    assert {"claimed": False} in marks  # the trigger Lambda gives nothing back either
    note, outside = ee._sigterm_next_step()
    assert outside is False and "counts as an attempt" in note
    assert ee._give_back_own_attempt() is False
    assert _row(phase3, "Utah")["attempts"] == 1


def test_a_deferred_stop_still_gives_its_attempt_back(phase3):
    assert ee._claim_enrich_attempt() is True
    ee._defer_on_stop_cause(stop_cause.EXIT_FUNDING)
    ee._attempt_counts()  # run_phase calls it after every deferral: nothing left to consume
    row = _row(phase3, "Utah")
    assert row["attempts"] == 0 and row["defer_cause"] == "funding"
    assert ee._failure_counted() is False


def test_the_trigger_does_not_give_back_a_counted_failure(table, monkeypatch):
    """The marker says claimed=False after the failure, so an outside stop reported by ECS
    afterwards forgives nothing."""
    import lambda_handlers.trigger_handler as th
    from src.utils import task_marker

    monkeypatch.setattr(th, "dynamo", table)
    pq.claim_attempt(table, "Utah", 3, task_id="t1")
    task_marker.mark(table, "t1", claimed=True, book="Utah")
    task_marker.mark(table, "t1", claimed=False)
    facts = {
        "family": th.TASK_FAMILIES[th.PHASE3_TASK_DEF],
        "book": "Utah",
        "task_id": "t1",
    }
    assert th._give_back_for_task(facts, task_marker.read(table, "t1")) is False
    assert _row(table, "Utah")["attempts"] == 1
