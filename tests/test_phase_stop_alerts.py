"""Task 1.1 (2026-11-18): every way a phase stops or degrades silently now sends ONE actionable
alert through the existing path (observe_error -> phase2-complete topic -> email + Slack).

Covered: preflight aborts (no credits, bad key, NAT never ready), a SIGTERM'd task, a crashed
wrapper, OpenSERP unavailable, the reconcile failing or its barrier timing out, a short
bibliography download, a failed or skipped post-sync validation. The watchdog (which already
notifies) is not alerted twice. No AWS call is made: observe_error is captured.
"""

import os
import signal
from types import SimpleNamespace
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402


@pytest.fixture
def alerts(monkeypatch):
    """Capture observe_error calls in ecs_entrypoint; reset the once-per-task flag."""
    sent = []

    def _capture(scope, event, *, note="", **fields):
        sent.append({"scope": scope, "event": event, "note": note, **fields})
        return "error"

    monkeypatch.setattr(ee, "observe_error", _capture)
    # the stop alert reports delivery (task 1.1b): captured the same way
    monkeypatch.setattr(
        ee, "observe_alert", lambda *a, **k: _capture(*a, **k) and False
    )
    monkeypatch.setattr(ee, "_stop_alerted", False)
    monkeypatch.setattr(ee, "_current_phase_script", "phase3_enrich_data.py")
    monkeypatch.setenv("BOOK_NAME", "USA-E-XChannel")
    return sent


def _one(sent, event):
    hits = [a for a in sent if a["event"] == event]
    assert len(hits) == 1, sent
    a = hits[0]
    assert (
        a["note"] and len(a["note"]) <= 300
    ), "note must say what to do and fit observe's cap"
    return a


@pytest.mark.parametrize(
    "status,text,event",
    [(402, "", "grok_credits_depleted"), (403, "no credit left", "grok_credits_depleted"),
     (401, "", "grok_key_invalid")],
)  # fmt: skip
def test_a_preflight_abort_alerts(alerts, monkeypatch, status, text, event):
    monkeypatch.setenv("GROK_API_KEY", "k")
    # what happens next comes from the queue deferral (task 1.2b, tests/test_queue_deferral.py)
    monkeypatch.setattr(ee, "_queue_followup", lambda cause, reason: f"NEXT-{cause}")
    resp = SimpleNamespace(status_code=status, text=text)
    with mock.patch("requests.post", return_value=resp):
        with pytest.raises(SystemExit):
            ee._preflight_credit_check()
    a = _one(alerts, event)
    assert a["scope"] == "phase"
    assert a["book"] == "USA-E-XChannel" and a["phase"] == "phase3_enrich_data"
    cause = "funding" if event == "grok_credits_depleted" else "config"
    assert a["note"].endswith(f"NEXT-{cause}")


def test_nat_never_ready_alerts(alerts, monkeypatch):
    monkeypatch.setenv("NAT_READY_MAX_ATTEMPTS", "1")
    monkeypatch.setattr(ee.time, "sleep", lambda *_: None)
    lam = mock.MagicMock()
    lam.invoke.side_effect = RuntimeError("boom")
    monkeypatch.setattr(ee.boto3, "client", lambda *a, **k: lam)
    monkeypatch.setattr(ee, "_remove_lock", lambda *_: None)
    with pytest.raises(SystemExit):
        ee._ensure_nat_if_needed("phase3_enrich_data.py")
    assert _one(alerts, "nat_not_ready")["attempts"] == 1


def test_sigterm_alerts_once(alerts, monkeypatch):
    for name in ("_final_sync", "_remove_lock", "_release_nat_lease"):
        monkeypatch.setattr(ee, name, lambda *a, **k: None)
    monkeypatch.setattr(ee, "_requeue_interrupted_resume", lambda: None)
    with pytest.raises(SystemExit) as stop:
        ee._handle_sigterm(signal.SIGTERM, None)
    assert stop.value.code == 143
    _one(alerts, "task_stopped")
    with pytest.raises(SystemExit):
        ee._handle_sigterm(signal.SIGTERM, None)
    assert len(alerts) == 1, "a second stop of the same task must not alert again"


def test_a_stop_already_notified_is_not_alerted_again(alerts, monkeypatch):
    """The watchdog calls _notify_failure, then SIGTERMs itself: one alert, not two."""
    published = []
    monkeypatch.setattr(ee, "publish_alert", lambda s, m: published.append(s) or True)
    ee._notify_failure("phase3_enrich_data.py", -1)
    for name in ("_final_sync", "_remove_lock", "_release_nat_lease"):
        monkeypatch.setattr(ee, name, lambda *a, **k: None)
    monkeypatch.setattr(ee, "_requeue_interrupted_resume", lambda: None)
    with pytest.raises(SystemExit):
        ee._handle_sigterm(signal.SIGTERM, None)
    assert len(published) == 1 and "FAILED" in published[0]
    assert alerts == []


def test_the_failure_notice_names_the_book(monkeypatch):
    published = []
    monkeypatch.setattr(
        ee, "publish_alert", lambda s, m: published.append((s, m)) or True
    )
    monkeypatch.setenv("BOOK_NAME", "USA-E-XChannel")
    ee._notify_failure("phase3_enrich_data.py", 2)
    subject, message = published[0]
    assert (
        "FAILED" in subject and "USA-E-XChannel" in message and "exit code 2" in message
    )


def test_a_wrapper_crash_alerts_and_reraises(alerts, monkeypatch):
    monkeypatch.setattr(ee, "run_phase", mock.MagicMock(side_effect=KeyError("x")))
    with pytest.raises(KeyError):
        ee._dispatch(["ecs_entrypoint.py", "phase3_enrich_data.py"])
    assert _one(alerts, "crashed")["error"] == "KeyError"


def test_a_deliberate_exit_is_not_a_crash(alerts, monkeypatch):
    monkeypatch.setattr(ee, "run_phase", mock.MagicMock(side_effect=SystemExit(1)))
    with pytest.raises(SystemExit):
        ee._dispatch(["ecs_entrypoint.py", "phase3_enrich_data.py"])
    assert alerts == []


def test_main_goes_through_the_dispatch():
    src = ee.Path(ee.__file__).read_text(encoding="utf-8")
    assert "    _dispatch(sys.argv)\n" in src[src.index('if __name__ == "__main__":') :]


@pytest.mark.parametrize("failure", ["timeout", "error"])
def test_openserp_unavailable_alerts(alerts, monkeypatch, failure):
    ecs = mock.MagicMock()
    ecs.describe_services.return_value = {
        "services": [{"runningCount": 0, "desiredCount": 0}]
    }
    if failure == "error":
        ecs.describe_services.side_effect = RuntimeError("denied")
    monkeypatch.setattr(ee.boto3, "client", lambda *a, **k: ecs)
    monkeypatch.setattr(ee, "_get_openserp_alb_dns", lambda: "")
    monkeypatch.setattr("time.sleep", lambda *_: None)
    ee._start_openserp_if_needed("phase3_enrich_data.py")
    a = _one(alerts, "unavailable")
    assert a["scope"] == "openserp" and a["book"] == "USA-E-XChannel"


def test_openserp_is_not_checked_outside_phase3(alerts):
    ee._start_openserp_if_needed("phase2_extract.py")
    assert alerts == []


def test_a_failed_reconcile_alerts(alerts, monkeypatch):
    s3 = mock.MagicMock()
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(
        ee, "_clear_reconcile_barrier", mock.MagicMock(side_effect=RuntimeError("x"))
    )
    ee._reconcile_bibliography_full_corpus()
    assert _one(alerts, "failed")["scope"] == "reconcile"


def test_a_failed_post_sync_validation_alerts(alerts, monkeypatch):
    monkeypatch.setattr(ee, "_post_sync_referential_enforce", lambda *_: None)
    monkeypatch.setattr(ee, "_refresh_validation_corpus", lambda **_: None)
    with mock.patch(
        "src.utils.provenance_integrity.run_for_phase", lambda *a, **k: None
    ):
        with mock.patch("scripts.validate_phase3.run", return_value=1):
            ee._phase3_durable_referential_fix()
    assert _one(alerts, "validation_failed")["exit_code"] == 1


def test_a_passing_post_sync_validation_is_silent(alerts, monkeypatch):
    monkeypatch.setattr(ee, "_post_sync_referential_enforce", lambda *_: None)
    monkeypatch.setattr(ee, "_refresh_validation_corpus", lambda **_: None)
    with mock.patch(
        "src.utils.provenance_integrity.run_for_phase", lambda *a, **k: None
    ):
        with mock.patch("scripts.validate_phase3.run", return_value=0):
            ee._phase3_durable_referential_fix()
    assert alerts == []


def test_a_validation_that_could_not_run_alerts(alerts, monkeypatch):
    monkeypatch.setattr(ee, "_post_sync_referential_enforce", lambda *_: None)
    monkeypatch.setattr(
        ee, "_refresh_validation_corpus", mock.MagicMock(side_effect=OSError("x"))
    )
    with mock.patch(
        "src.utils.provenance_integrity.run_for_phase", lambda *a, **k: None
    ):
        ee._phase3_durable_referential_fix()
    _one(alerts, "validation_not_run")


@pytest.mark.parametrize(
    "pending,event", [(0, "barrier_timeout"), (2, "barrier_timeout_incomplete")]
)
def test_a_reconcile_barrier_timeout_alerts(alerts, monkeypatch, pending, event):
    monkeypatch.setenv("RECONCILE_BARRIER_WAIT_SECS", "1")
    s3 = mock.MagicMock()
    s3.get_object.side_effect = Exception("404")
    monkeypatch.setattr(ee, "_s3_client", lambda: s3)
    monkeypatch.setattr(ee.time, "sleep", lambda n: None)
    monkeypatch.setattr(ee, "_pending_batches_for_book", lambda: pending)
    status = ee._wait_for_reconcile_barrier()
    assert status == ("timeout_incomplete" if pending else "timeout")
    assert _one(alerts, event)["scope"] == "reconcile"


def _stub_sigterm_steps(monkeypatch, *, sync_fails=False, requeue_fails=False):
    order = []

    def _sync(*_a, **_k):
        order.append("sync")
        if sync_fails:
            raise OSError("s3 down")

    def _requeue():
        order.append("requeue")
        if requeue_fails:
            raise RuntimeError("dynamo down")

    monkeypatch.setattr(ee, "_final_sync", _sync)
    monkeypatch.setattr(ee, "_remove_lock", lambda *a, **k: order.append("lock"))
    monkeypatch.setattr(ee, "_release_nat_lease", lambda: order.append("lease"))
    monkeypatch.setattr(ee, "_requeue_interrupted_resume", _requeue)
    return order


def test_sigterm_requeues_before_alerting(alerts, monkeypatch):
    order = _stub_sigterm_steps(monkeypatch)
    real = ee._alert_phase_stopped
    monkeypatch.setattr(
        ee,
        "_alert_phase_stopped",
        lambda *a, **k: (order.append("alert"), real(*a, **k)),
    )
    with pytest.raises(SystemExit):
        ee._handle_sigterm(signal.SIGTERM, None)
    assert order == ["sync", "lock", "lease", "requeue", "alert"]
    assert _one(alerts, "task_stopped")["synced"] is True


def test_sigterm_still_alerts_when_the_sync_fails(alerts, monkeypatch):
    order = _stub_sigterm_steps(monkeypatch, sync_fails=True, requeue_fails=True)
    with pytest.raises(SystemExit) as stop:
        ee._handle_sigterm(signal.SIGTERM, None)
    assert stop.value.code == 143
    # the lock and the NAT lease are released even though the upload failed
    assert order == ["sync", "lock", "lease", "requeue"]
    a = _one(alerts, "task_stopped")
    assert a["synced"] is False and "upload failed" in a["note"]


def test_a_suppressed_stop_alert_is_logged(alerts, monkeypatch, caplog):
    monkeypatch.setattr(ee, "_stop_alerted", True)
    with caplog.at_level("INFO"):
        ee._alert_phase_stopped("task_stopped", "x")
    assert alerts == []
    assert "phase.stop_alert_suppressed event=task_stopped" in caplog.text


def test_a_failed_failure_notice_does_not_suppress_the_next_alert(
    alerts, monkeypatch, caplog
):
    monkeypatch.setattr(ee, "publish_alert", lambda s, m: False)
    with caplog.at_level("WARNING"):
        ee._notify_failure("phase3_enrich_data.py", -1)
    assert "phase.failure_notice status=warn" in caplog.text
    _stub_sigterm_steps(monkeypatch)
    with pytest.raises(SystemExit):
        ee._handle_sigterm(signal.SIGTERM, None)
    _one(alerts, "task_stopped")


def test_the_failure_notice_keeps_the_book_on_one_line(monkeypatch):
    published = []
    monkeypatch.setattr(ee, "publish_alert", lambda s, m: published.append(m) or True)
    monkeypatch.setenv("BOOK_NAME", "Book\nInjected: line")
    ee._notify_failure("phase3_enrich_data.py", 1)
    first = published[0].splitlines()[0]
    assert "Book Injected: line" in first


def test_a_failing_alert_does_not_block_the_exit(monkeypatch):
    monkeypatch.setattr(ee, "_stop_alerted", False)
    monkeypatch.setattr(
        ee, "observe_error", mock.MagicMock(side_effect=RuntimeError("x"))
    )
    _stub_sigterm_steps(monkeypatch)
    with pytest.raises(SystemExit) as stop:
        ee._handle_sigterm(signal.SIGTERM, None)
    assert stop.value.code == 143


def test_an_undeliverable_nat_alert_is_called_out(alerts, monkeypatch, caplog):
    monkeypatch.setenv("NAT_READY_MAX_ATTEMPTS", "1")
    monkeypatch.setattr(ee.time, "sleep", lambda *_: None)
    lam = mock.MagicMock()
    lam.invoke.side_effect = RuntimeError("boom")
    monkeypatch.setattr(ee.boto3, "client", lambda *a, **k: lam)
    monkeypatch.setattr(ee, "_remove_lock", lambda *_: None)
    with caplog.at_level("ERROR"), pytest.raises(SystemExit):
        ee._ensure_nat_if_needed("phase3_enrich_data.py")
    assert "phase.alert_undeliverable status=error reason=no_nat" in caplog.text


def test_the_validation_note_names_the_real_log_marker():
    src = (ee.Path(ee.__file__).parent / "scripts" / "validate_phase3.py").read_text()
    assert "=== PHASE3 VALIDATION FAIL" in src
