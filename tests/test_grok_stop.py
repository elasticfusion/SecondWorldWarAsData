"""Task 1.2e (owner 2026-11-18, decision D8 and rule 3): when the shared Grok client's retries are
used up on an outage, or xAI refuses for lack of credit, the run STOPS with its own exit code
(the ECS wrapper defers the book; it resumes when Grok is back). An outage is never handed to a
caller as an ordinary error it could count as "no answer".
"""

from unittest import mock

import pytest
import requests

from src.utils import grok_models, stop_cause


@pytest.fixture(autouse=True)
def _fresh_stop(monkeypatch):
    monkeypatch.setattr(grok_models, "_stopped", {})


@pytest.fixture
def client(monkeypatch):
    from src.grok_client import GrokClient

    c = GrokClient.__new__(
        GrokClient
    )  # no config/secrets: only the API path is exercised
    return c


def _http_error(status):
    resp = mock.Mock(status_code=status)
    return requests.HTTPError(response=resp)


@pytest.mark.parametrize(
    "error",
    [requests.ConnectionError("no route"), requests.Timeout("slow"), _http_error(503),
     _http_error(429)],
)  # fmt: skip
def test_an_outage_after_the_retries_stops_the_run(client, monkeypatch, error):
    monkeypatch.setattr(client, "_call_api_retrying", mock.Mock(side_effect=error))
    with pytest.raises(grok_models.GrokUnreachable):
        client._call_api([{"role": "user", "content": "x"}])
    # every later call in this process stops too, before touching the network
    with pytest.raises(grok_models.GrokUnreachable):
        grok_models.ensure_running()


def test_a_client_error_is_not_an_outage(client, monkeypatch):
    monkeypatch.setattr(
        client, "_call_api_retrying", mock.Mock(side_effect=_http_error(400))
    )
    with pytest.raises(requests.HTTPError):
        client._call_api([])
    grok_models.ensure_running()  # not stopped


def test_no_credits_stops_the_run_for_funding(client):
    with pytest.raises(grok_models.GrokFundsExhausted):
        client._handle_api_errors(mock.Mock(status_code=402, text=""))


@pytest.mark.parametrize("text", ["forbidden", "card credit declined in prompt echo"])
def test_a_mid_run_403_is_an_ordinary_error(client, text):
    """Only a 402 stops the run mid-way: a 403 whose text merely says "credit" must not stop the
    whole run after paid work (security review). The preflight still checks 403-credit.
    """
    from src.grok_client import GrokAPIError

    with pytest.raises(GrokAPIError):
        client._handle_api_errors(mock.Mock(status_code=403, text=text))


def test_the_stops_are_handled_like_a_missing_model():
    """Every stop handler written for GrokModelUnavailable (thread pools, gather, ensure_running)
    stops for these too; none is an Exception, so no fail-safe handler absorbs them."""
    for cls in (grok_models.GrokUnreachable, grok_models.GrokFundsExhausted):
        assert issubclass(cls, grok_models.GrokModelUnavailable)
        assert not issubclass(cls, Exception)


@pytest.mark.parametrize(
    "cls,code",
    [(grok_models.GrokUnreachable, stop_cause.EXIT_CONNECTIVITY),
     (grok_models.GrokFundsExhausted, stop_cause.EXIT_FUNDING)],
)  # fmt: skip
def test_a_phase_maps_the_stop_to_its_exit_code(cls, code):
    def main():
        raise cls("x")

    assert stop_cause.run_main(main) == code
    assert stop_cause.cause_for_exit(code) in ("connectivity", "funding")


def test_other_exits_are_unchanged():
    assert stop_cause.run_main(lambda: 0) == 0
    assert stop_cause.run_main(lambda: None) is None
    with pytest.raises(grok_models.GrokModelUnavailable):
        stop_cause.run_main(
            mock.Mock(side_effect=grok_models.GrokModelUnavailable("gone"))
        )


@pytest.mark.parametrize("script", ["phase2_extract.py", "phase3_enrich_data.py"])
def test_the_phase_scripts_exit_through_run_main(script):
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / script).read_text(encoding="utf-8")
    tail = src[src.rindex('if __name__ == "__main__":') :]
    assert "run_main(main)" in tail


def test_an_outage_stop_expires_for_long_lived_processes(monkeypatch):
    with pytest.raises(grok_models.GrokUnreachable):
        grok_models.stop_run(grok_models.GrokUnreachable, "t", "down")
    with pytest.raises(grok_models.GrokUnreachable):
        grok_models.ensure_running()
    grok_models._stopped["transient"]["until"] = 0  # the stop window has passed
    grok_models.ensure_running()  # a warm Lambda may call again
    assert grok_models._stopped == {}


def test_a_missing_model_stop_does_not_expire():
    grok_models._stopped.update(reason="gone")
    grok_models.clear_transient_stop()
    with pytest.raises(grok_models.GrokModelUnavailable):
        grok_models.ensure_running()


def test_the_optional_metadata_request_survives_an_outage(monkeypatch):
    from src.ingestion import book_metadata

    monkeypatch.setattr(book_metadata, "enabled", lambda: True)

    def down(*a, **k):
        grok_models.stop_run(grok_models.GrokUnreachable, "grok_client", "no route")

    monkeypatch.setattr(book_metadata, "derive", down)
    monkeypatch.setattr(book_metadata, "opening_text", lambda d: "")
    monkeypatch.setattr(book_metadata, "page_hint", lambda h: None)
    monkeypatch.setattr(book_metadata, "title_pages", lambda d: [])
    assert book_metadata.for_import(object(), grok=object()) == {}
    grok_models.ensure_running()  # the import goes on and may call Grok again


def test_a_later_outage_never_lifts_a_missing_model_stop(monkeypatch):
    """Review finding: model gone, then an outage in another thread - the permanent stop must
    win and never expire (a retired model is not called again)."""
    monkeypatch.setattr(
        grok_models, "observe_error", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr("src.utils.observe.observe_error", lambda *a, **k: None)
    monkeypatch.setattr("src.utils.observe.publish_alert", lambda *a, **k: True)
    with pytest.raises(grok_models.GrokModelUnavailable):
        grok_models.model_unavailable("grok-x", "t")
    with pytest.raises(grok_models.GrokUnreachable):
        grok_models.stop_run(grok_models.GrokUnreachable, "t", "down")
    grok_models._stopped["transient"]["until"] = 0
    grok_models.clear_transient_stop()
    with pytest.raises(grok_models.GrokModelUnavailable) as e:
        grok_models.ensure_running()
    assert type(e.value) is grok_models.GrokModelUnavailable


def test_an_outage_first_then_a_missing_model_still_stops_for_the_model(monkeypatch):
    monkeypatch.setattr("src.utils.observe.observe_error", lambda *a, **k: None)
    monkeypatch.setattr("src.utils.observe.publish_alert", lambda *a, **k: True)
    with pytest.raises(grok_models.GrokUnreachable):
        grok_models.stop_run(grok_models.GrokUnreachable, "t", "down")
    with pytest.raises(grok_models.GrokModelUnavailable):
        grok_models.model_unavailable("grok-x", "t")
    grok_models.clear_transient_stop()
    with pytest.raises(grok_models.GrokModelUnavailable):
        grok_models.ensure_running()


def test_the_sameness_check_treats_an_outage_as_a_failed_compare():
    """trigger_handler catches the transient stops BEFORE the missing-model stop they derive
    from: an outage is not 'fix api.grok.model' (review finding)."""
    import os

    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    import lambda_handlers.trigger_handler as th

    assert grok_models.GrokUnreachable in th._grok_transient_stops()
    src = open(th.__file__, encoding="utf-8").read()
    assert src.index("except _grok_transient_stops()") < src.index(
        "except _grok_model_unavailable()"
    )


def test_the_preflight_defers_an_outage_as_connectivity(monkeypatch):
    import os

    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import ecs_entrypoint as ee

    sent = []
    monkeypatch.setenv("GROK_API_KEY", "k")
    monkeypatch.setattr(ee, "_alert_phase_stopped", lambda e, n, **k: sent.append(e))
    monkeypatch.setattr(ee, "_queue_followup", lambda cause, reason: cause)
    monkeypatch.setattr(
        "requests.post", mock.Mock(side_effect=grok_models.GrokUnreachable("down"))
    )
    with pytest.raises(SystemExit):
        ee._preflight_credit_check()
    assert sent == ["grok_connectivity"]
