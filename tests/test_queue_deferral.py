"""Task 1.2b (owner 2026-11-18: "deferred yes, dropped never"): a Phase 3 book is deferred, never
dropped. funding/connectivity stops use no attempt and hold every book until the retry interval;
failure (past the cap) and config stops are held until scripts/resume_book.py. Run against moto, so
the DynamoDB update / condition expressions are checked by a real expression parser.
"""

import os
from unittest import mock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402
from scripts import resume_book  # noqa: E402
from src.utils import pending_queue as pq  # noqa: E402
from src.utils import stop_cause  # noqa: E402

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
        t = ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield t


@pytest.fixture(autouse=True)
def fresh_task(monkeypatch):
    """Per-task module state in the wrapper starts clean in every test."""
    for name in ("_stop_alerted", "_watchdog_stop", "_attempt_claimed"):
        monkeypatch.setattr(ee, name, False)


def _row(table, book):
    return table.get_item(Key={"cache_key": f"pending#enrich#{book}"}).get("Item")


def _deferred(table, book, cause, reason="x"):
    """A queued book, then deferred (defer only marks an existing row)."""
    pq.queue_enrich(table, book)
    assert pq.defer(table, book, cause, reason) == cause


# --- pending_queue ---------------------------------------------------------------------------


def test_past_the_cap_the_book_is_deferred_not_dropped(table):
    for _ in range(3):
        assert pq.claim_attempt(table, "Utah", 3)[1] is True
    assert pq.claim_attempt(table, "Utah", 3) == (4, False)
    row = _row(table, "Utah")
    assert row["defer_cause"] == "failure" and row["attempts"] == 4
    assert "failed 3 times" in row["defer_reason"]
    assert pq.pending_books(table, "enrich") == ["Utah"]  # still queued
    assert pq.runnable_books(table, "enrich") == []  # but not launched


def test_a_failure_deferral_is_held_until_resumed(table):
    pq.queue_enrich(table, "Omaha")
    _deferred(table, "Utah", "failure", "x")
    assert pq.runnable_books(table, "enrich") == ["Omaha"]  # other books still run
    assert pq.resume(table, "Utah") is True
    row = _row(table, "Utah")
    assert row["attempts"] == 0 and "defer_cause" not in row
    assert pq.runnable_books(table, "enrich") == ["Omaha", "Utah"]


@pytest.mark.parametrize("cause", ["funding", "connectivity"])
def test_a_funding_or_connectivity_stop_holds_every_book_until_the_retry(table, cause):
    pq.queue_enrich(table, "Omaha")
    _deferred(table, "Utah", cause)
    now = int(_row(table, "Utah")["deferred_at"])
    assert pq.runnable_books(table, "enrich", 50, now=now + 49 * 60) == []
    assert pq.next_book(table, "enrich") == ""
    assert pq.runnable_books(table, "enrich", 50, now=now + 50 * 60) == [
        "Omaha",
        "Utah",
    ]


def test_a_new_run_clears_the_deferral(table):
    _deferred(table, "Utah", "funding", "x")
    assert pq.claim_attempt(table, "Utah", 3) == (1, True)
    assert "defer_cause" not in _row(table, "Utah")


def test_give_back_undoes_one_attempt_and_never_recreates_a_finished_row(table):
    pq.claim_attempt(table, "Utah", 3)
    pq.claim_attempt(table, "Utah", 3)
    assert pq.give_back(table, "Utah") is True
    assert _row(table, "Utah")["attempts"] == 1
    pq.finish(table, "enrich", "Utah")
    assert pq.give_back(table, "Utah") is False
    assert _row(table, "Utah") is None
    _deferred(table, "Omaha", "config", "x")  # a row with no attempts yet
    assert pq.give_back(table, "Omaha") is False


def test_resume_of_an_unknown_book_creates_nothing(table):
    assert pq.resume(table, "Nowhere") is False
    assert _row(table, "Nowhere") is None


def test_an_unknown_cause_is_refused(table):
    with pytest.raises(ValueError):
        pq.defer(table, "Utah", "bored", "")


def test_a_finished_book_is_never_re_queued_by_a_deferral(table):
    assert pq.defer(table, "Utah", "config", "x") == ""
    assert _row(table, "Utah") is None


def test_the_reason_is_stored_on_one_line_and_capped(table):
    _deferred(table, "Utah", "failure", "a\nb\tc " + "z" * 600)
    reason = _row(table, "Utah")["defer_reason"]
    assert reason.startswith("a b c ") and len(reason) == 500


def test_past_the_cap_a_failing_defer_still_refuses_the_run(table, monkeypatch, caplog):
    for _ in range(3):
        pq.claim_attempt(table, "Utah", 3)
    monkeypatch.setattr(pq, "defer", mock.Mock(side_effect=RuntimeError("throttled")))
    assert pq.claim_attempt(table, "Utah", 3) == (4, False)  # fail closed
    assert "OBSERV queue.defer_failed" in caplog.text


def test_a_capped_row_never_looks_runnable_mid_claim(table, monkeypatch):
    """The counting update must not clear the deferral: a reconcile reading between it and the
    re-defer would launch the capped book."""
    _deferred(table, "Utah", "failure")
    table.update_item(
        Key={"cache_key": "pending#enrich#Utah"},
        UpdateExpression="SET attempts = :n",
        ExpressionAttributeValues={":n": 3},
    )
    seen = []
    real = pq.defer

    def spy(*a, **k):
        seen.append(pq.runnable_books(table, "enrich"))
        return real(*a, **k)

    monkeypatch.setattr(pq, "defer", spy)
    assert pq.claim_attempt(table, "Utah", 3)[1] is False
    assert seen == [[]]


def test_deferred_rows_lists_cause_reason_and_attempts(table):
    pq.claim_attempt(table, "Utah", 3)
    _deferred(table, "Utah", "connectivity", "NAT not ready")
    pq.queue_enrich(table, "Omaha")
    [row] = pq.deferred_rows(table)
    assert (row["book"], row["cause"], row["reason"], row["attempts"]) == (
        "Utah",
        "connectivity",
        "NAT not ready",
        1,
    )


@pytest.mark.parametrize(
    "cfg,want",
    [({}, 50), ({"queue": {"defer_retry_minutes": 120}}, 120),
     ({"queue": {"defer_retry_minutes": 0}}, 50), ({"queue": {"defer_retry_minutes": "x"}}, 50)],
)  # fmt: skip
def test_the_retry_interval_is_configurable(cfg, want):
    assert pq.defer_retry_minutes(cfg) == want


def test_stop_cause_exit_codes():
    assert stop_cause.cause_for_exit(stop_cause.EXIT_FUNDING) == "funding"
    assert stop_cause.cause_for_exit(stop_cause.EXIT_CONNECTIVITY) == "connectivity"
    for ordinary in (1, 3, 4, 75, 143):
        assert stop_cause.cause_for_exit(ordinary) is None


# --- the ECS wrapper -------------------------------------------------------------------------


@pytest.fixture
def phase3(table, monkeypatch):
    monkeypatch.setattr(ee, "_cache_table", lambda: table)
    monkeypatch.setattr(ee, "_current_phase_script", "phase3_enrich_data.py")
    monkeypatch.setenv("BOOK_NAME", "Utah")
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    return table


@pytest.mark.parametrize(
    "cause,says",
    [("funding", "nothing to re-trigger"), ("config", "resume_book.py Utah")],
)
def test_a_preflight_stop_defers_and_gives_the_attempt_back(phase3, cause, says):
    assert ee._claim_enrich_attempt() is True
    note = ee._queue_followup(cause, "why")
    row = _row(phase3, "Utah")
    assert row["defer_cause"] == cause and row["attempts"] == 0  # decision 4(b)
    assert says in note


def test_other_phases_are_told_to_re_trigger(phase3, monkeypatch):
    monkeypatch.setattr(ee, "_current_phase_script", "phase2_extract.py")
    assert "re-trigger" in ee._queue_followup("funding", "why")
    assert _row(phase3, "Utah") is None


def test_a_defer_failure_is_logged_and_the_alert_says_the_attempt_counts(
    phase3, monkeypatch, caplog
):
    monkeypatch.setattr(ee, "_cache_table", mock.Mock(side_effect=RuntimeError("x")))
    note = ee._queue_followup("funding", "why")
    assert "still counts" in note
    assert "OBSERV queue.enrich_defer_failed" in caplog.text


def test_the_credit_preflight_defers_the_book_for_funding(phase3, monkeypatch):
    sent = []
    monkeypatch.setattr(
        ee, "_alert_phase_stopped", lambda e, n, **k: sent.append((e, n))
    )
    monkeypatch.setenv("GROK_API_KEY", "k")
    resp = mock.Mock(status_code=402, text="no credits")
    monkeypatch.setattr("requests.post", lambda *a, **k: resp)
    ee._claim_enrich_attempt()
    with pytest.raises(SystemExit):
        ee._preflight_credit_check()
    assert _row(phase3, "Utah")["defer_cause"] == "funding"
    assert sent[0][0] == "grok_credits_depleted" and "deferred (funding)" in sent[0][1]
    assert len(sent[0][1]) <= 300  # observe's note cap


@pytest.mark.parametrize(
    "code,cause",
    [
        (stop_cause.EXIT_FUNDING, "funding"),
        (stop_cause.EXIT_CONNECTIVITY, "connectivity"),
    ],
)
def test_a_phase_exit_code_defers_with_its_cause(phase3, code, cause):
    ee._claim_enrich_attempt()
    ee._defer_on_stop_cause(code)
    row = _row(phase3, "Utah")
    assert row["defer_cause"] == cause and row["attempts"] == 0


def test_an_ordinary_failure_counts_and_is_not_deferred(phase3):
    ee._claim_enrich_attempt()
    ee._defer_on_stop_cause(1)
    row = _row(phase3, "Utah")
    assert "defer_cause" not in row and row["attempts"] == 1


def test_the_next_book_chain_skips_a_held_queue(phase3):
    pq.queue_enrich(phase3, "Omaha")
    assert ee._get_next_pending_enrich() == "Omaha"
    _deferred(phase3, "Brest", "connectivity", "x")
    assert (
        ee._get_next_pending_enrich() == ""
    )  # held: OpenSERP may stop, nothing is launched


def test_the_reconcile_skips_held_books(phase3, monkeypatch):
    import lambda_handlers.trigger_handler as th

    monkeypatch.setattr(th, "dynamo", phase3)
    pq.queue_enrich(phase3, "Omaha")
    _deferred(phase3, "Utah", "failure", "x")
    assert th._get_pending_books_for_enrich() == ["Omaha"]
    _deferred(phase3, "Brest", "funding", "x")
    assert th._get_pending_books_for_enrich() == []


# --- scripts/resume_book.py ------------------------------------------------------------------


def test_resume_book_lists_and_resumes(table, capsys):
    _deferred(table, "Utah", "failure", "Phase 3 failed 3 times")
    _deferred(table, "Omaha", "config", "bad key")
    assert resume_book.main(["list"], table=table) == 0
    out = capsys.readouterr().out
    assert "Utah\tfailure" in out and "Omaha\tconfig" in out
    assert resume_book.main(["Utah"], table=table) == 0
    assert [r["book"] for r in pq.deferred_rows(table)] == ["Omaha"]
    assert resume_book.main(["--all", "--cause", "config"], table=table) == 0
    assert pq.deferred_rows(table) == []


def test_resume_book_fails_on_an_unknown_or_unsafe_book(table, capsys):
    assert resume_book.main(["Nowhere"], table=table) == 1
    assert resume_book.main(["a#b"], table=table) == 1
    assert "error: ValueError" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["--cause", "failure"], ["--all", "Utah"]])
def test_resume_book_rejects_bad_arguments(table, argv):
    with pytest.raises(SystemExit):
        resume_book.main(argv, table=table)


# --- review fixes: model unavailable, SIGTERM (Spot), partial failures, the failure notice ----


def test_a_model_unavailable_stop_alerts_with_the_resume_command(phase3, monkeypatch):
    from src.utils import grok_models

    sent = []
    monkeypatch.setattr(
        ee, "_alert_phase_stopped", lambda e, n, **k: sent.append((e, n))
    )
    monkeypatch.setenv("GROK_API_KEY", "k")
    monkeypatch.setattr(
        "requests.post", mock.Mock(side_effect=grok_models.GrokModelUnavailable("gone"))
    )
    ee._claim_enrich_attempt()
    with pytest.raises(SystemExit):
        ee._preflight_credit_check()
    assert _row(phase3, "Utah")["defer_cause"] == "config"
    assert sent[0][0] == "grok_model_unavailable"
    assert "resume_book.py Utah" in sent[0][1] and len(sent[0][1]) <= 300


def test_an_outside_sigterm_gives_the_attempt_back(phase3, monkeypatch):
    """A Spot reclaim, an ECS stop or a deploy is not the book's fault (owner 2026-11-18)."""
    ee._claim_enrich_attempt()
    note, give_back = ee._sigterm_next_step()
    assert give_back and "no re-trigger needed" in note
    ee._give_back_own_attempt()
    row = _row(phase3, "Utah")
    assert (
        row["attempts"] == 0 and "defer_cause" not in row
    )  # queued, runs next reconcile


def test_a_watchdog_sigterm_still_counts(phase3, monkeypatch):
    monkeypatch.setattr(
        ee, "_stop_alerted", True
    )  # the watchdog alerted: the run stalled
    ee._claim_enrich_attempt()
    note, give_back = ee._sigterm_next_step()
    assert "counts as an attempt" in note and give_back is False
    assert _row(phase3, "Utah")["attempts"] == 1


def test_the_sigterm_alert_says_what_happens_next(phase3, monkeypatch):
    sent, order = [], []
    for name in (
        "_final_sync",
        "_remove_lock",
        "_release_nat_lease",
        "_requeue_interrupted_resume",
    ):
        monkeypatch.setattr(ee, name, lambda *a, **k: None)
    monkeypatch.setattr(
        ee,
        "_alert_phase_stopped",
        lambda e, n, **k: sent.append(n) or order.append("alert"),
    )
    real = ee._give_back_own_attempt
    monkeypatch.setattr(
        ee, "_give_back_own_attempt", lambda: order.append("give_back") or real()
    )
    ee._claim_enrich_attempt()
    with pytest.raises(SystemExit):
        ee._handle_sigterm(15, None)
    assert "no re-trigger needed" in sent[0] and len(sent[0]) <= 300
    assert order == ["alert", "give_back"]  # a slow DynamoDB call cannot cost the alert
    assert _row(phase3, "Utah")["attempts"] == 0


def test_a_book_without_a_row_is_told_to_re_trigger(phase3):
    assert "re-trigger" in ee._queue_followup("funding", "why")
    assert _row(phase3, "Utah") is None  # nothing re-created


def test_a_failed_give_back_keeps_the_deferral_and_says_so(phase3, monkeypatch, caplog):
    ee._claim_enrich_attempt()
    monkeypatch.setattr(pq, "give_back", mock.Mock(side_effect=RuntimeError("x")))
    note = ee._queue_followup("funding", "why")
    assert _row(phase3, "Utah")["defer_cause"] == "funding"
    assert (
        "OBSERV queue.give_back_failed" in caplog.text and "deferred (funding)" in note
    )


def test_the_failure_notice_carries_the_next_step(monkeypatch):
    sent = []
    monkeypatch.setattr(
        ee, "publish_alert", lambda subj, body: sent.append(body) or True
    )
    ee._notify_failure(
        "phase3_enrich_data.py", 81, "The book is deferred (connectivity)"
    )
    assert "The book is deferred (connectivity).\n" in sent[0]
    ee._notify_failure("phase3_enrich_data.py", 1)
    assert "deferred" not in sent[1]


# --- second review round: watchdog, give back once, clear failure, long slugs, config ------


def test_a_watchdog_stop_counts_even_when_its_alert_was_not_delivered(
    phase3, fresh_task, monkeypatch
):
    """SNS down: _stop_alerted stays False, but a stall must not be forgiven (it would loop)."""
    ee._claim_enrich_attempt()
    ee._mark_watchdog_stop()
    note, give_back = ee._sigterm_next_step()
    assert "counts as an attempt" in note and give_back is False
    assert _row(phase3, "Utah")["attempts"] == 1


def test_an_attempt_is_given_back_once_only(phase3, fresh_task):
    ee._claim_enrich_attempt()
    ee._claim_enrich_attempt()  # a previous run's attempt is on the row too
    ee._queue_followup("connectivity", "why")
    assert ee._give_back_own_attempt() is False  # a later SIGTERM in the same task
    assert _row(phase3, "Utah")["attempts"] == 1


def test_a_sigterm_before_the_claim_gives_nothing_back(phase3, fresh_task):
    pq.claim_attempt(phase3, "Utah", 3)  # an earlier run's real failure
    assert ee._give_back_own_attempt() is False  # this task never claimed
    assert _row(phase3, "Utah")["attempts"] == 1


def test_a_failed_deferral_clear_still_runs_and_says_how_to_fix(
    table, monkeypatch, caplog
):
    _deferred(table, "Utah", "funding")
    real = table.update_item

    def flaky(**kw):
        if kw.get("UpdateExpression", "").startswith("REMOVE"):
            raise RuntimeError("throttled")
        return real(**kw)

    monkeypatch.setattr(table, "update_item", flaky)
    assert pq.claim_attempt(table, "Utah", 3) == (1, True)
    assert (
        "OBSERV queue.defer_clear_failed" in caplog.text
        and "resume_book.py Utah" in caplog.text
    )


def test_a_long_slug_keeps_the_resume_command_in_the_alert(
    phase3, fresh_task, monkeypatch
):
    slug = "A" * 120
    monkeypatch.setenv("BOOK_NAME", slug)
    pq.queue_enrich(phase3, slug)
    sent = []
    monkeypatch.setattr(
        ee, "observe_error", lambda s, e, note="", **k: sent.append(note)
    )
    for _ in range(4):
        ee._claim_enrich_attempt()
    assert f"resume_book.py {slug}" in sent[0][:300]


def test_the_next_book_uses_the_default_interval_if_config_is_unreadable(
    phase3, monkeypatch
):
    monkeypatch.setattr(
        "src.utils.config.load_config", mock.Mock(side_effect=RuntimeError("bad yaml"))
    )
    pq.queue_enrich(phase3, "Omaha")
    assert ee._get_next_pending_enrich() == "Omaha"


def test_resume_book_prints_no_control_characters(table, capsys):
    pq.queue_enrich(table, "Utah")
    table.update_item(
        Key={"cache_key": "pending#enrich#Utah"},
        UpdateExpression="SET defer_cause = :c, defer_reason = :r",
        ExpressionAttributeValues={":c": "failure", ":r": "bad\x1b[31mred"},
    )
    resume_book.main(["list"], table=table)
    assert "\x1b" not in capsys.readouterr().out


def test_a_failed_defer_means_no_later_give_back(phase3, monkeypatch):
    ee._claim_enrich_attempt()
    monkeypatch.setattr(pq, "defer", mock.Mock(side_effect=RuntimeError("x")))
    assert "still counts" in ee._queue_followup("funding", "why")
    assert ee._give_back_own_attempt() is False  # as the alert said
    assert _row(phase3, "Utah")["attempts"] == 1


def test_the_watchdog_flags_its_stop_before_it_sends_sigterm(monkeypatch):
    seen = []
    monkeypatch.setattr(ee, "_heartbeat_nat_lease", lambda: None)
    monkeypatch.setattr(ee, "_notify_failure", lambda *a, **k: None)
    monkeypatch.setattr(ee.os, "kill", lambda pid, sig: seen.append(ee._watchdog_stop))
    sync = ee.BackgroundSync(60)
    monkeypatch.setattr(sync, "_upload_changed", lambda: None)
    sync._last_activity_time = 0  # stalled since the epoch
    sync._sync()
    assert seen == [True]


def test_run_phase_defers_on_a_connectivity_exit(phase3, monkeypatch):
    from tests.test_phase3_lock_release import _stub_run_phase_to_failure

    _stub_run_phase_to_failure(monkeypatch, [])
    pq.claim_attempt(phase3, "Utah", 3)
    monkeypatch.setattr(
        ee, "_claim_enrich_attempt", lambda: ee._mark_attempt_claimed() or True
    )
    monkeypatch.setattr(
        ee.subprocess,
        "run",
        lambda *a, **k: mock.Mock(returncode=stop_cause.EXIT_CONNECTIVITY),
    )
    with pytest.raises(SystemExit):
        ee.run_phase("phase3_enrich_data.py", [])
    row = _row(phase3, "Utah")
    assert row["defer_cause"] == "connectivity" and row["attempts"] == 0


def test_repeated_connectivity_stops_end_in_a_failure_hold(table):
    """1.2b follow-up (MED, built with the exit-code producers in 1.2e): a book that keeps
    stopping for connectivity would retry hourly forever; past the cap it is held instead.
    """
    pq.queue_enrich(table, "Utah")
    assert [pq.defer(table, "Utah", "connectivity", "x", 3) for _ in range(3)] == [
        "connectivity"
    ] * 3
    assert pq.defer(table, "Utah", "connectivity", "NAT", 3) == "failure"
    row = _row(table, "Utah")
    assert row["defer_cause"] == "failure" and "4 times since" in row["defer_reason"]
    assert pq.runnable_books(table, "enrich") == []
    pq.resume(table, "Utah")  # the operator's resume starts the count again
    assert "connectivity_stops" not in _row(table, "Utah")


def test_funding_stops_have_their_own_higher_cap(table):
    pq.queue_enrich(table, "Utah")
    got = [pq.defer(table, "Utah", "funding", "x", 1, 3) for _ in range(4)]
    assert got == ["funding"] * 3 + ["failure"]
    pq.resume(table, "Utah")
    assert "funding_stops" not in _row(table, "Utah")


def test_the_alert_says_when_repeated_stops_held_the_book(phase3, monkeypatch):
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {"queue": {"max_connectivity_deferrals": 1}},
    )
    ee._claim_enrich_attempt()
    ee._queue_followup("connectivity", "a")
    note = ee._queue_followup("connectivity", "b")
    assert "too many times" in note and "resume_book.py Utah" in note
