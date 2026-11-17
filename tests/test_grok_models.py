"""A model version that is gone fails loudly and stops the run (owner, 2026-11-12). Found live:
xAI answers grok-3-mini and grok-3-mini-fast with grok-4.3 (HTTP 200) and grok-2-latest with 404 -
the trigger's sameness check had failed on every call, and the "cheap" lookups ran on grok-4.3.
"""

import asyncio
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from src.utils import grok_models as G


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr("src.utils.observe.publish_alert", mock.Mock())
    monkeypatch.delenv("GROK_MODEL", raising=False)
    G._alerted.clear()
    G._stopped.clear()
    yield
    G._stopped.clear()


def _reply(model):
    return {
        "model": model,
        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
    }


# --- the rule ------------------------------------------------------------------------------------


def test_a_redirected_model_stops_with_an_alert():
    with mock.patch("src.utils.observe.observe_error") as err:
        with pytest.raises(G.GrokModelUnavailable, match="grok-3-mini"):
            G.check_served("grok-3-mini", _reply("grok-4.3"), "test")
    assert err.call_args.args[:2] == ("grok", "model_unavailable")
    assert err.call_args.kwargs["served"] == "grok-4.3"


@pytest.mark.parametrize(
    "requested, served, same",
    [
        ("grok-4.3", "grok-4.3", True),
        ("grok-4.3", "grok-4.3-0430", True),
        ("grok-4.3", "grok-4.3-20260430", True),
        ("grok-4.3-latest", "grok-4.3-0430", True),
        ("grok-4.3", "grok-4.3-mini", False),  # another model
        ("grok-4", "grok-4.3", False),
        ("grok-3-mini", "grok-4.3", False),
    ],
)
def test_the_served_model_must_be_the_requested_one(requested, served, same):
    assert G.served_matches(requested, served) is same


def test_an_answer_naming_no_model_is_not_judged():
    G.check_served("grok-4.3", {"choices": []}, "test")


def test_it_is_not_an_exception_so_no_fail_safe_handler_absorbs_it():
    """The extractors' `except Exception` must not turn a missing model into "no entities"."""
    assert not issubclass(G.GrokModelUnavailable, Exception)
    with pytest.raises(G.GrokModelUnavailable):
        try:
            G.check_served("grok-3-mini", _reply("grok-4.3"), "test")
        except Exception:  # noqa: BLE001 - what every fail-safe extractor does
            pytest.fail("absorbed")


def test_parallel_stops_alert_once():
    with mock.patch("src.utils.observe.observe_error") as err:
        for _ in range(3):
            with pytest.raises(G.GrokModelUnavailable):
                G.check_served("grok-x", _reply("grok-4.3"), "test")
    assert err.call_count == 1


@pytest.mark.parametrize(
    "status, text, missing",
    [
        (
            404,
            '{"code":"not-found","error":"The model grok-2-latest does not exist"}',
            True,
        ),
        (
            404,
            "The model grok-4.3 does not exist or your team does not have access to it.",
            True,
        ),
        (404, "Model grok-4.20-0309-reasoning not found", True),
        (400, "Model not found: grok-x", False),  # only a 404 means the model is gone
        (404, "The file you uploaded was not found; the model is fine", False),
        (404, "batch not found", False),
        (500, "The model grok-2 does not exist", False),
    ],
)
def test_a_missing_model_http_error(status, text, missing):
    assert G.is_model_missing(status, text) is missing


def test_batch_answers_must_come_from_a_configured_model():
    G.check_batch_served("grok-4.3", {"grok-4.3"}, "test")
    with pytest.raises(G.GrokModelUnavailable):
        G.check_batch_served("grok-4.3", {"grok-4.6"}, "test")


def test_the_configured_models():
    cfg = {"api": {"grok": {"model": "grok-4.3", "model_map": {"isbn": "grok-4.6"}}}}
    assert G.configured_models(cfg) == {"grok-4.3", "grok-4.6"}
    with mock.patch.dict(os.environ, {"GROK_MODEL": "grok-4.5"}):
        assert G.default_model(cfg) == "grok-4.5"
    assert G.default_model({}) == G.DEFAULT_MODEL == "grok-4.3"


def test_the_shipped_config_names_no_retired_model():
    """grok-3-mini / grok-2 are not served: the shipped config must not name them (the preflight
    would stop every phase)."""
    import yaml

    for name in ("config.yaml.example", "config.yaml"):
        path = Path(__file__).resolve().parent.parent / name
        if path.exists():
            models = G.configured_models(yaml.safe_load(path.read_text()))
            assert not any(m.startswith(("grok-3", "grok-2")) for m in models), (
                name,
                models,
            )


# --- the preflight -------------------------------------------------------------------------------


def _http(listed, served, aliases=None, status=200):
    """requests stub: the model list, and each probe answered by served[model] (None = 404)."""

    def get(url, **kw):
        models = [{"id": m, "aliases": (aliases or {}).get(m, [])} for m in listed]
        return SimpleNamespace(
            raise_for_status=lambda: None, json=lambda: {"models": models}
        )

    def post(url, json=None, **kw):
        s = served.get(json["model"])
        if s is None:
            return SimpleNamespace(
                status_code=404, text="The model does not exist", json=dict
            )
        return SimpleNamespace(status_code=status, text="", json=lambda: _reply(s))

    return mock.patch.multiple("requests", get=get, post=post)


def test_the_preflight_passes_when_every_model_answers_as_itself():
    with _http(
        ["grok-4.3", "grok-4.6"], {"grok-4.3": "grok-4.3", "grok-4.6": "grok-4.6"}
    ):
        G.preflight("k", {"grok-4.3", "grok-4.6"})


def test_the_preflight_stops_on_an_unlisted_model():
    with _http(["grok-4.3"], {"grok-4.3": "grok-4.3"}):
        with pytest.raises(G.GrokModelUnavailable, match="not in xAI's model list"):
            G.preflight("k", {"grok-3-mini"})


def test_the_preflight_stops_on_a_listed_but_redirected_model():
    with _http(["grok-old"], {"grok-old": "grok-4.3"}):
        with pytest.raises(G.GrokModelUnavailable, match="served by 'grok-4.3'"):
            G.preflight("k", {"grok-old"})


def test_the_preflight_stops_on_a_404():
    with _http(["grok-x"], {}):
        with pytest.raises(G.GrokModelUnavailable, match="HTTP 404"):
            G.preflight("k", {"grok-x"})


def test_a_model_listed_by_an_alias_is_listed():
    with _http(
        ["grok-4.3-0430"],
        {"grok-4.3": "grok-4.3-0430"},
        aliases={"grok-4.3-0430": ["grok-4.3"]},
    ):
        G.preflight("k", {"grok-4.3"})


def test_a_probe_that_cannot_answer_is_unverified_not_a_stop():
    with _http(["grok-4.3"], {"grok-4.3": "grok-4.3"}, status=429):
        with pytest.raises(G.PreflightUnverified):
            G.preflight("k", {"grok-4.3"})


def test_an_already_probed_model_is_only_list_checked():
    with _http(["grok-4.3"], {}):  # a probe would 404
        G.preflight("k", {"grok-4.3"}, probed={"grok-4.3"})


# --- the callers ---------------------------------------------------------------------------------


def _client(tmp_path, monkeypatch):
    import src.grok_client as gc
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    return gc.GrokClient(DiskCacheBackend(tmp_path))


def test_a_live_call_answered_by_another_model_stops(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = SimpleNamespace(status_code=200, json=lambda: _reply("grok-4.3"), text="")
    with mock.patch.object(client, "_post_with_deadline", return_value=resp):
        with pytest.raises(G.GrokModelUnavailable):
            client._call_api([{"role": "user", "content": "x"}], 0.1, "grok-3-mini")


def test_a_live_call_to_a_missing_model_stops_not_retries(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = SimpleNamespace(
        status_code=404, text="The model grok-x does not exist", headers={}, json=dict
    )
    with mock.patch.object(client, "_post_with_deadline", return_value=resp) as post:
        with pytest.raises(G.GrokModelUnavailable):
            client._call_api([{"role": "user", "content": "x"}], 0.1, "grok-x")
    assert post.call_count == 1


def test_batch_results_carry_and_check_the_answering_model(monkeypatch):
    from src.utils import batch_api as B

    monkeypatch.setattr(G, "configured_models", lambda config=None: {"grok-4.3"})
    B.verify_served_models([B.BatchResult("r1", "x", model="grok-4.3")], "test")
    with pytest.raises(G.GrokModelUnavailable):
        B.verify_served_models([B.BatchResult("r2", "x", model="grok-4.7")], "test")


def test_a_gather_hands_the_stop_on_after_saving_completed_links(tmp_path, monkeypatch):
    """asyncio.gather(return_exceptions=True) returns the stop as a result: the entity types that
    completed still get their links saved, then the run stops."""
    from src.extraction import batch_parallel as bp

    book = tmp_path / "content" / "B"
    book.mkdir(parents=True)
    event_file, parsed_file = book / "c1-event.json", book / "c1-parsed.json"
    event_file.write_text(
        json.dumps({"Event": {"EventID": "E", "Sub-events": [{"Sub-eventID": "S1"}]}})
    )
    parsed_file.write_text(json.dumps({"book": "B"}))

    async def ok(*a, **k):
        return {"count": 1, "links": {"S1": ["P1"]}}

    async def gone(*a, **k):
        raise G.GrokModelUnavailable("x")

    for name in (
        "extract_dates_batch_async",
        "extract_people_groups_batch_async",
        "extract_people_batch_async",
    ):
        monkeypatch.setattr(bp, name, ok)
    monkeypatch.setattr(bp, "extract_places_batch_async", gone)
    # the minimal test event is not schema-complete: write it as is
    monkeypatch.setattr(
        bp,
        "write_json_with_lock",
        lambda path, data: Path(path).write_text(json.dumps(data)),
    )
    with pytest.raises(G.GrokModelUnavailable):
        asyncio.run(
            bp.extract_all_async(event_file, parsed_file, mock.Mock(), tmp_path, {})
        )
    saved = json.loads(event_file.read_text())["Event"]["Sub-events"][0]
    assert saved["people"] == ["P1"] and not saved.get("places")


def test_a_chapter_that_stops_is_not_counted_as_a_failed_chapter():
    from src.extraction import batch_parallel as bp

    results = {"processed": 0, "failed": 0}
    with mock.patch.object(bp, "observe_error") as err:
        bp._process_batch_results(
            [(None, "c1", None)], [G.GrokModelUnavailable("x")], results, mock.Mock()
        )
    assert results["failed"] == 0 and not err.called


def test_the_phase_preflight_stops_the_task(monkeypatch):
    import ecs_entrypoint as ee

    monkeypatch.setenv("GROK_API_KEY", "k")
    ok = SimpleNamespace(
        status_code=200,
        text="",
        raise_for_status=lambda: None,
        json=lambda: _reply("grok-4.3"),
    )
    with (
        mock.patch("requests.post", return_value=ok),
        mock.patch.object(G, "preflight", side_effect=G.GrokModelUnavailable("x")),
    ):
        with pytest.raises(SystemExit) as stop:
            ee._preflight_credit_check()
    assert stop.value.code == 1


def test_the_phase_preflight_stops_when_the_default_itself_is_redirected(monkeypatch):
    import ecs_entrypoint as ee

    monkeypatch.setenv("GROK_API_KEY", "k")
    monkeypatch.setenv("GROK_MODEL", "grok-3-mini")
    redirected = SimpleNamespace(
        status_code=200,
        text="",
        raise_for_status=lambda: None,
        json=lambda: _reply("grok-4.3"),
    )
    with mock.patch("requests.post", return_value=redirected):
        with pytest.raises(SystemExit):
            ee._preflight_credit_check()


def test_the_trigger_sameness_check_stops_on_a_missing_model():
    import urllib.error

    import lambda_handlers.trigger_handler as th

    err = urllib.error.HTTPError(
        "u",
        404,
        "nf",
        {},
        io.BytesIO(b'{"error":"The model grok-2-latest does not exist"}'),
    )
    with mock.patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(G.GrokModelUnavailable):
            th._sameness_call(SimpleNamespace(), "grok-2-latest")


def test_the_trigger_holds_an_upload_when_its_sameness_model_is_gone(monkeypatch):
    """A Lambda must not crash (and be re-invoked) on the stop: the upload is held for review."""
    import lambda_handlers.trigger_handler as th

    monkeypatch.setattr(th, "_title_claim", lambda title: ("final", "llm:old"))
    monkeypatch.setattr(th, "_multi_fingerprint", lambda s3, keys: "fp")
    monkeypatch.setattr(
        th, "_llm_same_work", mock.Mock(side_effect=G.GrokModelUnavailable("x"))
    )
    proceed, _reason, category = th._resolve_title_identity(None, "T", ["a", "b"])
    assert (proceed, category) == (False, "model-unavailable")


def test_after_a_stop_no_call_reaches_the_network(tmp_path, monkeypatch):
    """A thread pool's queued work must not each make a billed call to the redirected model."""
    with pytest.raises(G.GrokModelUnavailable):
        G.check_served("grok-x", _reply("grok-4.3"), "test")
    client = _client(tmp_path, monkeypatch)
    with mock.patch.object(client, "_post_with_deadline") as post:
        with pytest.raises(G.GrokModelUnavailable, match="stopped earlier"):
            client._call_api([{"role": "user", "content": "x"}], 0.1, "grok-4.3")
    post.assert_not_called()


def test_a_bio_enrichment_worker_hands_the_stop_on():
    from src.extraction import bio_enrich_parallel as bep

    def gone(person, client):
        raise G.GrokModelUnavailable("x")

    with pytest.raises(G.GrokModelUnavailable):
        bep._fetch_one({"name": "A"}, None, gone)
    assert bep._fetch_one({"name": "A"}, None, lambda p, c: 1 / 0)[2] is not None


def test_a_held_upload_alert_says_what_to_do():
    import lambda_handlers.trigger_handler as th

    assert (
        "Fix api.grok.model" in th._review_alert("model-unavailable")["alert_message"]
    )
    assert th._review_alert("duplicate") == {}
