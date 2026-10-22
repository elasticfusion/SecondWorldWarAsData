"""Batch A (structured side-channel migration): noaa + open-elevation now emit traceable
fetch-provenance events (kind=structured) through the shared primitive.
"""

import json

import pytest

from src.utils import fetch_provenance as fp


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    def __init__(self, resp):
        self._resp = resp

    def request(self, method, url, **kwargs):
        return self._resp


@pytest.fixture(autouse=True)
def _local(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_table", lambda: None)
    monkeypatch.setattr(fp, "_LOCAL_DIR", tmp_path / "_fe")
    return tmp_path / "_fe"


def _events(store, provider):
    f = store / f"{provider}.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_noaa_emits_structured_provenance(_local, monkeypatch):
    from src.enrichment import noaa_weather
    from src.utils import http_pool

    monkeypatch.setattr(noaa_weather, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        http_pool, "get_session", lambda: _Session(_Resp(200, {"results": []}))
    )
    noaa_weather._get("stations", "TESTTOKEN", {"datasetid": "GHCND"})
    evs = _events(_local, "noaa")
    assert evs and evs[0]["provider"] == "noaa"
    assert evs[0]["kind"] == fp.STRUCTURED
    assert evs[0]["outcome"] == fp.HIT
    # The secret token is a HEADER (not a recorded param) — ensure it never leaked into params.
    assert "TESTTOKEN" not in json.dumps(evs[0]["request_params"])


def test_elevation_emits_structured_provenance(_local, tmp_path):
    from src.enrichment.elevation_verify import terrain_elevation

    sess = _Session(_Resp(200, {"results": [{"elevation": 318.0}]}))
    out = terrain_elevation(48.65, -0.93, tmp_path / "cache", session=sess)
    assert out == 318.0
    evs = _events(_local, "open-elevation")
    assert evs and evs[0]["kind"] == fp.STRUCTURED
    assert evs[0]["http_method"] == "POST"
    assert evs[0]["outcome"] == fp.HIT
