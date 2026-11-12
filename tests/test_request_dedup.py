"""Exact duplicate requests are paid for once. Equipment prepares a chapter's items in parallel, so
"Sherman" asked twice at the same moment must share one live call (single flight); in batch
mode the collector folds identical requests silently and warns only when two DIFFERENT requests
share a key; and identity resolution treats "Sherman" / "SHERMAN  " as one designation.
"""

import json
import threading
import time
from unittest import mock

import pytest

import src.grok_client as gc
from src.extraction import equipment_disambiguation as dis
from src.utils.batch_api import BatchCollector, BatchRequest
from src.utils.cache_backend import DiskCacheBackend


@pytest.fixture(autouse=True)
def _no_book():
    token = gc.current_book.set(None)
    yield
    gc.current_book.reset(token)


@pytest.fixture
def grok(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    monkeypatch.delenv("GROK_CACHE_DISABLED", raising=False)
    return gc.GrokClient(DiskCacheBackend(tmp_path / "c"))


def _reply(body):
    return {"choices": [{"message": {"content": json.dumps(body)}}]}


def _together(fn, n=4):
    """Run fn() on n threads released at once; return their results."""
    start, out = threading.Barrier(n), [None] * n

    def run(i):
        start.wait()
        out[i] = fn()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return out


def test_identical_concurrent_requests_make_one_live_call(grok):
    calls = []

    def api(*a, **k):
        calls.append(1)
        time.sleep(0.2)  # the others arrive while it is in flight
        return _reply({"canonical_name": "M4 Sherman"})

    with mock.patch.object(grok, "_call_api", side_effect=api):
        got = _together(
            lambda: grok.extract_json("Sherman?", cache_type="equipment_disambiguation")
        )
    assert len(calls) == 1
    assert got == [{"canonical_name": "M4 Sherman"}] * 4


def test_different_requests_are_not_held_up(grok):
    calls = []

    def api(messages, *a, **k):
        calls.append(messages[-1]["content"])
        return _reply({"ok": 1})

    with mock.patch.object(grok, "_call_api", side_effect=api):
        prompts = iter(["A?", "B?", "C?"])
        lock = threading.Lock()

        def ask():
            with lock:
                p = next(prompts)
            return grok.extract_json(p, cache_type="equipment_disambiguation")

        _together(ask, n=3)
    assert sorted(calls) == ["A?", "B?", "C?"]


def test_if_the_first_request_fails_a_waiter_asks_itself(grok):
    calls = []

    def api(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            time.sleep(0.2)
            raise gc.GrokAPIError("first failed")
        return _reply({"ok": 2})

    def ask():
        try:
            return grok.extract_json("X?", cache_type="equipment_disambiguation")
        except gc.GrokAPIError:
            return "failed"

    with mock.patch.object(grok, "_call_api", side_effect=api):
        got = _together(ask, n=2)
    assert sorted(map(str, got)) == ["failed", "{'ok': 2}"]
    assert grok._in_flight == {}  # nothing left claimed


def _req(content, rid="k1"):
    return BatchRequest(
        rid,
        [{"role": "user", "content": content}],
        "m",
        0.0,
        "equipment_disambiguation",
    )


def test_an_identical_request_is_folded_silently(caplog):
    c = BatchCollector()
    c.add(_req("Sherman?"))
    c.add(_req("Sherman?"))
    assert len(c) == 1 and c.duplicates == 1
    assert "duplicate_request_id_dropped" not in caplog.text


def test_two_different_requests_on_one_key_are_flagged(caplog):
    c = BatchCollector()
    c.add(_req("Sherman?"))
    c.add(_req("Panther?"))
    assert len(c) == 1 and c.duplicates == 0
    assert "duplicate_request_id_dropped" in caplog.text


def test_case_and_spacing_do_not_make_a_new_designation(tmp_path, monkeypatch):
    monkeypatch.setattr(dis, "_LEARNED_PATH", tmp_path / "learned.yaml")
    monkeypatch.setattr(dis, "_SUGGESTIONS_PATH", tmp_path / "s.jsonl")
    monkeypatch.setattr(dis, "_CACHE", {})
    monkeypatch.setattr(dis, "_LEARNED_CACHE", None)
    keys = []

    class Client:
        def _get_model(self, _k):
            return "m"

        def extract_json(self, prompt, cache_key=None, **_k):
            keys.append(cache_key)
            return {"canonical_name": "Zorbatron Mk I", "confidence": 0.9}

    assert dis._designation_key("  Zorbatron   GUN ") == "zorbatron gun"
    dis._try_grok("Zorbatron gun", dis._designation_key("Zorbatron gun"), Client())
    dis._try_grok("ZORBATRON  gun ", dis._designation_key("ZORBATRON  gun "), Client())
    assert (
        len(keys) == 2 and keys[0] == keys[1]
    )  # one cache entry: the second is a hit live
