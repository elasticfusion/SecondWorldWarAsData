"""batch.min_requests: fewer queued requests are answered live (a batch can take 24 h)."""

import os

import pytest

from src.grok_client import GrokClient
from src.utils.batch_api import BatchCollector, BatchRequest, batch_min_requests


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    monkeypatch.delenv("BATCH_MIN_REQUESTS", raising=False)
    c = GrokClient(cache_dir=tmp_path, batch_mode=True)
    c._config = {"batch": {"min_requests": 3}}
    c._batch_collector = BatchCollector()
    return c


def _queue(c, n, model="grok-x"):
    for i in range(n):
        c._batch_collector.add(
            BatchRequest(
                f"rid{i}", [{"role": "user", "content": f"q{i}"}], model, 0.1, "events"
            )
        )


def _answers(content="ok"):
    calls = []

    def call(messages, temperature=0.1, model=""):
        calls.append(model)
        return {"choices": [{"message": {"content": content}}]}

    return calls, call


def test_threshold_from_config_env_and_default(monkeypatch):
    monkeypatch.delenv("BATCH_MIN_REQUESTS", raising=False)
    assert batch_min_requests({}) == 50
    assert batch_min_requests({"batch": {"min_requests": 7}}) == 7
    monkeypatch.setenv("BATCH_MIN_REQUESTS", "0")
    assert batch_min_requests({"batch": {"min_requests": 7}}) == 0
    monkeypatch.setenv("BATCH_MIN_REQUESTS", "x")
    assert batch_min_requests({}) == 50


def test_a_small_batch_is_answered_live_and_cached(client, monkeypatch):
    _queue(client, 2)
    calls, call = _answers("answer")
    monkeypatch.setattr(client, "_call_api", call)
    monkeypatch.setattr(
        client, "_submit_poll_retrieve", lambda *a, **k: pytest.fail("batched")
    )
    batch_id = client.submit_batch(batch_name="phase2-B-r1-2reqs")
    assert batch_id.startswith(
        "live-2reqs"
    )  # non-empty: the caller re-runs from the cache
    assert calls == ["grok-x", "grok-x"]  # each request with its own model
    assert client._get_cache("events")["rid0"] == "answer"
    assert client._batch_collector is None and client.batch_mode is False


def test_a_failed_live_answer_is_reported(client, monkeypatch):
    import src.grok_client as G

    _queue(client, 2)
    warned = []
    monkeypatch.setattr(G, "observe_warn", lambda *a, **k: warned.append(k))

    def boom(*a, **k):
        raise RuntimeError("503")

    monkeypatch.setattr(client, "_call_api", boom)
    assert client.submit_batch(batch_name="b").startswith("live-")
    assert warned and warned[0]["failed"] == 2


def test_at_the_threshold_it_batches(client, monkeypatch):
    _queue(client, 3)
    monkeypatch.setattr(
        client, "_answer_small_batch_live", lambda *a: pytest.fail("answered live")
    )

    class Sent(Exception):
        pass

    def sent(*a, **k):
        raise Sent

    monkeypatch.setattr(client, "_submit_poll_retrieve", sent)
    with pytest.raises(Sent):
        client.submit_batch(batch_name="b")
