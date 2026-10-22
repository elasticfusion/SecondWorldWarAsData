"""Unit tests for the universal fetch-provenance primitive (src/utils/fetch_provenance.py).

No network: a fake session returns canned responses. Storage is forced to local JSONL by
monkeypatching _table() -> None, written under a tmp output dir.
"""

import json
from pathlib import Path

import pytest

from src.utils import fetch_provenance as fp


class _FakeResp:
    def __init__(self, status_code=200, text="ok"):
        self.status_code = status_code
        self.text = text


class _FakeSession:
    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp or _FakeResp()
        self._raise = raise_exc
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self._raise:
            raise self._raise
        return self._resp


@pytest.fixture(autouse=True)
def _local_store(tmp_path, monkeypatch):
    """Force local JSONL mode under tmp."""
    monkeypatch.setattr(fp, "_table", lambda: None)
    monkeypatch.setattr(fp, "_LOCAL_DIR", tmp_path / "_fetch_events")
    return tmp_path / "_fetch_events"


def _read_events(store: Path, provider: str):
    f = store / f"{provider}.jsonl"
    if not f.exists():
        return []
    return [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]


def test_structured_hit_records_traceable_event(_local_store):
    sess = _FakeSession(_FakeResp(200))
    resp = fp.recorded_fetch(
        "nominatim",
        "https://nominatim.example/search",
        kind=fp.STRUCTURED,
        params={"q": "Bastogne"},
        session=sess,
        result_summary_fn=lambda r: "lat=50.0,lon=5.7",
    )
    assert resp.status_code == 200
    evs = _read_events(_local_store, "nominatim")
    assert len(evs) == 1
    e = evs[0]
    # Every traceability field present.
    assert e["provider"] == "nominatim"
    assert e["endpoint"] == "https://nominatim.example/search"
    assert e["request_params"] == {"q": "Bastogne"}
    assert e["http_method"] == "GET"
    assert e["http_status"] == 200
    assert e["outcome"] == fp.HIT
    assert e["result_summary"] == "lat=50.0,lon=5.7"
    assert e["kind"] == fp.STRUCTURED
    assert e["review_status"] is None  # structured -> no review
    assert e["queried_at"] and e["fetch_event_id"]
    assert isinstance(e["latency_ms"], int)


def test_non_2xx_is_miss(_local_store):
    sess = _FakeSession(_FakeResp(404))
    fp.recorded_fetch("nominatim", "https://x/y", kind=fp.STRUCTURED, session=sess)
    assert _read_events(_local_store, "nominatim")[0]["outcome"] == fp.MISS


def test_textual_event_is_pending_review(_local_store):
    sess = _FakeSession(_FakeResp(200))
    fp.recorded_fetch("wayback", "https://archive.org/x", kind=fp.TEXTUAL, session=sess)
    e = _read_events(_local_store, "wayback")[0]
    assert e["kind"] == fp.TEXTUAL
    assert e["review_status"] == fp.PENDING


def test_transport_error_records_and_reraises(_local_store):
    sess = _FakeSession(raise_exc=ConnectionError("boom"))
    with pytest.raises(ConnectionError):
        fp.recorded_fetch("noaa", "https://noaa/x", kind=fp.STRUCTURED, session=sess)
    e = _read_events(_local_store, "noaa")[0]
    assert e["outcome"] == fp.ERROR
    assert e["http_status"] is None
    assert "ConnectionError" in e["error"]


def test_secret_params_redacted(_local_store):
    sess = _FakeSession(_FakeResp(200))
    fp.recorded_fetch(
        "noaa",
        "https://noaa/x",
        kind=fp.STRUCTURED,
        params={"token": "SUPERSECRET", "q": "weather"},
        session=sess,
    )
    e = _read_events(_local_store, "noaa")[0]
    assert e["request_params"]["token"] == "<redacted>"
    assert e["request_params"]["q"] == "weather"


def test_record_fetch_event_direct(_local_store):
    """Callers holding a response (e.g. wayback courtesy wrapper) can record directly."""
    fid = fp.record_fetch_event(
        provider="wayback",
        endpoint="https://archive.org/wayback/available?url=x",
        kind=fp.TEXTUAL,
        http_status=200,
        outcome=fp.HIT,
        result_summary="snapshot 19960101",
    )
    assert fid
    e = _read_events(_local_store, "wayback")[0]
    assert e["result_summary"] == "snapshot 19960101"
    assert e["review_status"] == fp.PENDING


def test_persist_never_raises(_local_store, monkeypatch):
    """A persist failure must not propagate (fetch must never be blocked by telemetry).
    Break the DynamoDB path AND the local write to exercise _persist's internal guard.
    """
    monkeypatch.setattr(fp, "_table", lambda: (_ for _ in ()).throw(OSError("boom")))
    # Point the local dir at a path that can't be created (a file, not a dir).
    bad = _local_store.parent / "not_a_dir"
    bad.write_text("x")
    monkeypatch.setattr(fp, "_LOCAL_DIR", bad / "sub")
    # Should NOT raise despite both storage paths failing.
    fid = fp.record_fetch_event(
        provider="nominatim", endpoint="https://x", kind=fp.STRUCTURED
    )
    assert fid
