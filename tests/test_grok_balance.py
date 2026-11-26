"""Task 1.1c (owner 2026-11-18): Grok credit under $50 halts a Grok phase at start; at or under
$10 during a run a loud hourly alert, the run goes on; an unreadable balance stops, never
guesses. The live balance is the invoice preview's credit minus this month's use (the ledger
total lags a month: 2026-11-18 ledger $192.59, console $62.31)."""

import os
from unittest import mock

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402
from src.utils import grok_balance as gb  # noqa: E402

TEAM = "8b976bb7-6e12-47fd-ae9f-c10b20d0811f"
# the 2026-11-18 answer (amounts only), whose balance the xAI console showed as $62.31
PREVIEW = {
    "coreInvoice": {
        "totalWithCorr": {"val": "13028"},
        "prepaidCredits": {"val": "-19259"},
        "prepaidCreditsUsed": {"val": "-13028"},
    },
    "effectiveSpendingLimit": "0",
    "billingCycle": {"year": 2026, "month": 11},
}


class _Resp:
    def __init__(self, code=200, body=None, text_only=False):
        self.status_code = code
        self._body = body
        self._text_only = text_only

    def json(self):
        if self._text_only:
            raise ValueError("not json")
        return self._body


class _Session:
    """Answers the team lookup and the invoice preview; records the calls."""

    def __init__(self, preview=None, team=None, raise_on=None):
        self.preview = preview or _Resp(body=PREVIEW)
        self.team = team or _Resp(body={"team_id": TEAM, "api_key_id": "k"})
        self.raise_on = raise_on
        self.calls = []

    def get(self, url, headers, timeout, allow_redirects):
        assert allow_redirects is False  # a redirect must not carry the key anywhere
        self.calls.append((url, headers["Authorization"]))
        if self.raise_on and self.raise_on in url:
            raise TimeoutError("slow")
        return self.team if url == gb.API_KEY_URL else self.preview


def test_the_live_balance_is_credit_minus_this_months_use():
    s = _Session()
    assert gb.balance_usd("mgmt", "inference", s) == 62.31
    # the team comes from the inference key; the preview is read with the management key
    assert s.calls == [
        (gb.API_KEY_URL, "Bearer inference"),
        (gb.PREVIEW_URL.format(team=TEAM), "Bearer mgmt"),
    ]


def test_the_team_is_looked_up_once_per_process():
    s = _Session()
    gb.balance_usd("mgmt", "inference", s)
    gb.balance_usd("mgmt", "inference", s)
    assert [u for u, _ in s.calls].count(gb.API_KEY_URL) == 1


@pytest.mark.parametrize(
    "session,cause",
    [
        (_Session(preview=_Resp(401)), "config"),
        (_Session(preview=_Resp(403)), "config"),
        (_Session(team=_Resp(401)), "config"),
        (_Session(preview=_Resp(404)), "config"),
        (_Session(preview=_Resp(503)), "connectivity"),
        (_Session(preview=_Resp(429)), "connectivity"),
        (_Session(raise_on="management-api"), "connectivity"),
        (_Session(preview=_Resp(text_only=True)), "config"),
        (_Session(preview=_Resp(body=["x"])), "config"),
        (_Session(preview=_Resp(body={"coreInvoice": {}})), "config"),
        (_Session(preview=_Resp(body={"coreInvoice": {
            "prepaidCredits": {"val": "-1e3"}, "prepaidCreditsUsed": {"val": "0"}}})), "config"),
        (_Session(preview=_Resp(body={"coreInvoice": {
            "prepaidCredits": {"val": str(-10**12)}, "prepaidCreditsUsed": {"val": "0"}}})),
         "config"),
        (_Session(team=_Resp(body={"team_id": "../../x"})), "config"),
        (_Session(team=_Resp(body={"team_id": "\u0661\u0662"})), "config"),
        (_Session(preview=_Resp(302)), "config"),
        (_Session(preview=_Resp(body={"coreInvoice": "x"})), "config"),
        (_Session(preview=_Resp(body={"coreInvoice": {
            "prepaidCredits": "x", "prepaidCreditsUsed": [1]}})), "config"),
        (_Session(preview=_Resp(body={"coreInvoice": {
            "prepaidCredits": {"val": str(-10**7 - 1)}, "prepaidCreditsUsed": {"val": "0"}}})),
         "config"),
        (_Session(team=_Resp(body={})), "config"),
    ],
)  # fmt: skip
def test_an_unreadable_balance_is_never_guessed(session, cause):
    with pytest.raises(gb.BalanceUnreadable) as e:
        gb.balance_usd("mgmt", "inference", session)
    assert e.value.cause == cause


def test_no_management_key_is_unreadable_config(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "inference")
    with pytest.raises(gb.BalanceUnreadable) as e:
        gb.balance_usd(session=_Session())
    assert e.value.cause == "config" and gb.MANAGEMENT_KEY_ENV in e.value.reason


def test_thresholds_default_to_the_owners_and_never_to_zero():
    assert gb.thresholds({}) == (50.0, 10.0, 15)
    assert gb.thresholds(None) == (50.0, 10.0, 15)
    cfg = {"api": {"grok": {"credit_floor_usd": 75, "credit_alert_usd": "20"}}}
    assert gb.thresholds(cfg) == (75.0, 20.0, 15)
    bad = {"api": {"grok": {"credit_floor_usd": 0, "credit_alert_usd": "x"}}}
    assert gb.thresholds(bad)[:2] == (50.0, 10.0)
    # an alert line at or over the floor could never fire: the owner's defaults
    inverted = {"api": {"grok": {"credit_floor_usd": 20, "credit_alert_usd": 30}}}
    assert gb.thresholds(inverted)[:2] == (50.0, 10.0)


def test_the_held_management_key_is_used_and_not_put_in_the_environment():
    gb.set_management_key(" held-key\n")
    s = _Session()
    gb.balance_usd(api_key="inference", session=s)
    assert s.calls[-1][1] == "Bearer held-key"
    assert gb.MANAGEMENT_KEY_ENV not in os.environ


def test_a_local_env_key_is_stripped(monkeypatch):
    monkeypatch.setenv(gb.MANAGEMENT_KEY_ENV, "env-key\n")
    s = _Session()
    gb.balance_usd(api_key="inference", session=s)
    assert s.calls[-1][1] == "Bearer env-key"


def test_the_monitor_alerts_at_or_under_the_line_only():
    alerts, unread = [], []
    values = iter([10.0, 10.01, 3.5])
    m = gb.LowBalanceMonitor(
        10.0, 15, alerts.append, unread.append, read=lambda: next(values)
    )
    assert [m.check(), m.check(), m.check()] == [10.0, 10.01, 3.5]
    assert alerts == [10.0, 3.5] and unread == []


def test_the_monitor_reports_an_unreadable_balance_and_never_raises():
    unread = []

    def boom():
        raise gb.BalanceUnreadable("connectivity", "invoice preview: HTTP 503")

    m = gb.LowBalanceMonitor(10.0, 15, lambda u: None, unread.append, read=boom)
    assert m.check() is None
    m = gb.LowBalanceMonitor(
        10.0, 15, lambda u: None, unread.append, read=lambda: 1 / 0
    )
    assert m.check() is None
    assert unread == ["invoice preview: HTTP 503", "ZeroDivisionError"]


def test_no_alert_once_the_monitor_is_stopped():
    alerts = []
    m = gb.LowBalanceMonitor(10.0, 15, alerts.append, lambda r: None, read=lambda: 1.0)
    m.stop()
    m.check()
    assert alerts == []


def test_the_monitor_thread_stops():
    m = gb.LowBalanceMonitor(
        10.0, 15, lambda u: None, lambda r: None, read=lambda: 99.0
    )
    m.start()
    m.stop()
    assert not m._thread.is_alive()


def test_the_hourly_bucket_repeats_the_alert_each_hour():
    assert gb.hour_bucket(7200) == gb.hour_bucket(10799) != gb.hour_bucket(10800)


# --- the ECS wrapper ------------------------------------------------------------------------------


@pytest.fixture
def stopped(monkeypatch):
    """Record the alert, the queue cause and the exit path of a preflight stop."""
    rec: dict = {"alerts": [], "queued": [], "freed": []}
    monkeypatch.setattr(ee, "_config_or_empty", lambda: {})
    monkeypatch.setattr(ee, "_balance_checked", False)
    monkeypatch.setattr(
        ee, "_alert_phase_stopped", lambda e, n, **k: rec["alerts"].append((e, n, k))
    )
    monkeypatch.setattr(
        ee, "_queue_followup", lambda c, r: rec["queued"].append(c) or "QUEUED"
    )
    monkeypatch.setattr(ee, "_remove_lock", lambda s: rec["freed"].append("lock"))
    monkeypatch.setattr(ee, "_release_nat_lease", lambda: rec["freed"].append("nat"))
    return rec


def test_a_balance_under_the_floor_stops_the_phase_and_defers_funding(
    monkeypatch, stopped
):
    monkeypatch.setattr(gb, "balance_usd", lambda: 49.99)
    with pytest.raises(SystemExit) as e:
        ee._preflight_balance_check("phase3_enrich_data.py")
    assert e.value.code == 1
    [(event, note, fields)] = stopped["alerts"]
    assert event == "grok_credits_low" and "$49.99" in note and "$50.00" in note
    assert "console.x.ai" in note and note.endswith("QUEUED")
    assert stopped["queued"] == ["funding"]  # resumes on its own after a top-up
    assert stopped["freed"] == ["lock", "nat"]  # lock freed at once (D10)


@pytest.mark.parametrize("cause", ["config", "connectivity"])
def test_an_unreadable_balance_stops_the_phase(monkeypatch, stopped, cause):
    def unreadable():
        raise gb.BalanceUnreadable(cause, "invoice preview: HTTP 503")

    monkeypatch.setattr(gb, "balance_usd", unreadable)
    with pytest.raises(SystemExit):
        ee._preflight_balance_check("phase2_extract.py")
    [(event, note, _)] = stopped["alerts"]
    assert event == "grok_balance_unreadable" and "never guesses" in note
    assert stopped["queued"] == [cause]
    assert ("xai-management-key" in note) == (cause == "config")


def test_a_balance_over_the_floor_lets_the_phase_start(monkeypatch, stopped, caplog):
    monkeypatch.setattr(gb, "balance_usd", lambda: 62.31)
    ee._preflight_balance_check("phase3_enrich_data.py")
    assert stopped["alerts"] == [] and stopped["freed"] == []
    assert "OBSERV grok.balance usd=62.31" in caplog.text


def test_the_balance_is_checked_once_per_task(monkeypatch, stopped):
    """A batch run_phase hands on to run_submit_only, which preflights again."""
    read = mock.Mock(return_value=62.31)
    monkeypatch.setattr(gb, "balance_usd", read)
    ee._preflight_balance_check("phase2_extract.py")
    ee._preflight_balance_check("phase2_extract.py")
    assert read.call_count == 1


def test_a_malformed_answer_still_stops_cleanly(monkeypatch, stopped):
    """An answer that is not a balance is unreadable (alert, defer, lock freed), not a crash."""
    s = _Session(preview=_Resp(body={"coreInvoice": {"prepaidCredits": "x"}}))
    real = gb.balance_usd
    monkeypatch.setattr(gb, "balance_usd", lambda: real("mgmt", "inference", s))
    with pytest.raises(SystemExit):
        ee._preflight_balance_check("phase3_enrich_data.py")
    assert stopped["queued"] == ["config"] and stopped["freed"] == ["lock", "nat"]


def test_submit_only_checks_the_balance_before_any_grok_spend(monkeypatch):
    order = []
    monkeypatch.setattr(ee, "_acquire_nat_lease", lambda: None)
    monkeypatch.setattr(ee, "_ensure_nat_if_needed", lambda s: None)
    monkeypatch.setattr(ee, "_clear_title_cache_if_reprocess", lambda s: None)
    monkeypatch.setattr(ee, "_load_secrets", lambda: order.append("secrets"))
    monkeypatch.setattr(
        ee, "_preflight_balance_check", lambda s: order.append(("balance", s))
    )

    def probe():
        order.append("probe")
        raise SystemExit(9)  # stop here: the rest of submit-only is not under test

    monkeypatch.setattr(ee, "_preflight_credit_check", probe)
    monkeypatch.setattr(ee, "WORKDIR", mock.MagicMock())
    with pytest.raises(SystemExit):
        ee.run_submit_only("phase2_extract.py", [])
    assert order == ["secrets", ("balance", "phase2_extract.py"), "probe"]


@pytest.mark.parametrize(
    "script,metadata",
    [("phase1_parse.py", "1"), ("phase0_ingest.py", "1"), ("phase0_convert.py", "0"),
     ("phase0_video.py", "0")],
)  # fmt: skip
def test_phases_that_spend_no_grok_are_not_checked(
    monkeypatch, stopped, script, metadata
):
    monkeypatch.setenv("BOOK_METADATA", metadata)
    monkeypatch.setattr(gb, "balance_usd", mock.Mock(side_effect=AssertionError))
    ee._preflight_balance_check(script)
    assert ee._start_balance_monitor(script) is None


@pytest.mark.parametrize("script", ["phase0_convert.py", "phase0_video.py"])
def test_an_import_that_asks_grok_is_halted_under_the_floor(
    monkeypatch, stopped, script
):
    """Owner 2026-11-18: deal with low credit early - at the import, not at Phase 2."""
    monkeypatch.setenv("BOOK_METADATA", "1")
    monkeypatch.setattr(gb, "balance_usd", lambda: 12.0)
    with pytest.raises(SystemExit):
        ee._preflight_balance_check(script)
    assert stopped["alerts"][0][0] == "grok_credits_low"


def test_the_check_can_be_switched_off_for_other_deployments(
    monkeypatch, stopped, caplog
):
    off = {"api": {"grok": {"balance_check": False}}}
    monkeypatch.setattr(ee, "_config_or_empty", lambda: off)
    monkeypatch.setattr(gb, "balance_usd", mock.Mock(side_effect=AssertionError))
    ee._preflight_balance_check("phase3_enrich_data.py")
    assert ee._start_balance_monitor("phase3_enrich_data.py") is None
    assert stopped["alerts"] == [] and "balance_check is off" in caplog.text


def test_only_an_explicit_false_switches_the_check_off():
    assert gb.check_enabled({}) and gb.check_enabled(None)
    assert gb.check_enabled({"api": {"grok": {"balance_check": "no"}}})
    assert not gb.check_enabled({"api": {"grok": {"balance_check": False}}})


def test_the_run_monitor_alerts_hourly_per_task(monkeypatch):
    monkeypatch.setattr(ee, "_config_or_empty", lambda: {})
    monkeypatch.setenv("ECS_TASK_ID", "t1")
    started = []
    monkeypatch.setattr(
        gb.LowBalanceMonitor, "start", lambda self: started.append(self)
    )
    sent = []
    monkeypatch.setattr(ee, "observe_alert", lambda s, e, **k: sent.append((e, k)))
    warned = []
    monkeypatch.setattr(ee, "observe_warn_counted", lambda s, e, **k: warned.append(e))
    m = ee._start_balance_monitor("phase3_enrich_data.py")
    assert started == [m]
    m._read = lambda: 9.0
    m.check()
    [(event, fields)] = sent
    assert event == "credits_critical" and "$9.00" in fields["note"]
    assert fields["dedup_key"] == f"t1.{gb.hour_bucket()}"
    m._read = mock.Mock(side_effect=gb.BalanceUnreadable("config", "x"))
    m.check()
    assert warned == ["balance_unreadable"]


@pytest.mark.parametrize(
    "script,loaded", [("phase3_enrich_data.py", True), ("phase1_parse.py", False)]
)
def test_the_management_key_is_loaded_for_grok_phases_only(monkeypatch, script, loaded):
    monkeypatch.setenv("GROK_API_KEY", "set")
    monkeypatch.setenv("NARA_API_KEY", "set")
    monkeypatch.setenv("SAFE_BROWSING_API_KEY", "set")
    monkeypatch.setenv("ENV_NAME", "dev")
    monkeypatch.setattr(ee, "_current_phase_script", script)
    sm = mock.Mock()
    sm.get_secret_value.return_value = {"SecretString": " mgmt-key \n"}
    monkeypatch.setattr(ee.boto3, "client", lambda *a, **k: sm)
    ee._load_secrets()
    if loaded:
        sm.get_secret_value.assert_called_once_with(
            SecretId="dev-wwii-pipeline/xai-management-key"
        )
        assert gb._management_key == {"key": "mgmt-key"}
    else:
        sm.get_secret_value.assert_not_called()
    # held in-process only: no phase subprocess (os.environ.copy()) inherits it
    assert gb.MANAGEMENT_KEY_ENV not in os.environ


def test_run_phase_stops_the_monitor_when_the_phase_ends(monkeypatch):
    from tests.test_phase3_lock_release import _stub_run_phase_to_failure

    order: list = []
    _stub_run_phase_to_failure(monkeypatch, order)
    monitor = mock.Mock()
    monkeypatch.setattr(ee, "_start_balance_monitor", lambda s: monitor)
    with pytest.raises(SystemExit):
        ee.run_phase("phase3_enrich_data.py", [])
    monitor.stop.assert_called_once()
