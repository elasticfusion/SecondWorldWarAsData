"""Orchestration-group regressions — Phase-2 review (HIGH).

1. credit reservation released on ANY post-reserve failure (poll/retrieve), not just submit
2. weather processed-gate: a processed event is skipped (not re-extracted) when should_reprocess off
3. batch_poller: a completed batch with a significant error fraction ALERTS (not silent)
4. BatchCollector.write_jsonl fails LOUD over an absolute request-count / byte cap
5. equipment + grok_search_maps reject a decompression bomb (MAX_IMAGE_PIXELS)
"""

import os
from unittest import mock

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


# --- #1 credit released on poll/retrieve failure -------------------------------------------
def test_credit_released_on_poll_or_retrieve_failure():
    import src.grok_client as gc
    from src.utils.batch_api import BatchCollector, BatchRequest

    client = gc.GrokClient.__new__(gc.GrokClient)
    client.api_key = "k"
    client.cache_dir = None  # force the early cache_dir check? no — set below
    import tempfile
    from pathlib import Path

    client.cache_dir = Path(tempfile.mkdtemp())
    client._config = {}
    client._estimate_batch_cost = lambda: 1.0
    coll = BatchCollector()
    coll.add(BatchRequest("r1", [{"role": "user", "content": "p"}], "m", 0.1, "events"))
    client._batch_collector = coll
    client.batch_mode = True

    released = {"n": 0}
    with (
        mock.patch("src.utils.credit_gate.reserve", return_value=True),
        mock.patch(
            "src.utils.credit_gate.release",
            side_effect=lambda *a, **k: released.__setitem__("n", released["n"] + 1),
        ),
        mock.patch("src.utils.batch_api.submit_batch", return_value="b1"),
        # poll FAILS -> the reservation must be released (the old bug leaked here)
        mock.patch(
            "src.utils.batch_api.poll_batch", side_effect=RuntimeError("poll boom")
        ),
    ):
        with pytest.raises(RuntimeError):
            client.submit_batch(batch_name="t")
    assert (
        released["n"] == 1
    ), "credit reservation was NOT released on poll failure (leak)"


def _mk_batch_client():
    import src.grok_client as gc
    import tempfile
    from pathlib import Path
    from src.utils.batch_api import BatchCollector, BatchRequest

    client = gc.GrokClient.__new__(gc.GrokClient)
    client.api_key = "k"
    client.cache_dir = Path(tempfile.mkdtemp())
    client._config = {}
    client._estimate_batch_cost = lambda: 1.0
    coll = BatchCollector()
    coll.add(BatchRequest("r1", [{"role": "user", "content": "p"}], "m", 0.1, "events"))
    client._batch_collector = coll
    client.batch_mode = True
    return client


def test_credit_released_on_retrieve_failure():
    client = _mk_batch_client()
    released = {"n": 0}
    with (
        mock.patch("src.utils.credit_gate.reserve", return_value=True),
        mock.patch(
            "src.utils.credit_gate.release",
            side_effect=lambda *a, **k: released.__setitem__("n", released["n"] + 1),
        ),
        mock.patch("src.utils.batch_api.submit_batch", return_value="b1"),
        mock.patch("src.utils.batch_api.poll_batch", return_value={"state": {}}),
        mock.patch(
            "src.utils.batch_api.retrieve_results_ordered",
            side_effect=RuntimeError("retrieve boom"),
        ),
    ):
        with pytest.raises(RuntimeError):
            client.submit_batch(batch_name="t")
    assert released["n"] == 1, "reservation not released on RETRIEVE failure (leak)"


def test_credit_not_released_on_success():
    client = _mk_batch_client()
    client._get_cache = lambda ct: {}
    client._sanitize_content = lambda c: c
    client._batch_prompt_preview = lambda rid, rbi: ""
    released = {"n": 0}
    from src.utils.batch_api import BatchResult

    with (
        mock.patch("src.utils.credit_gate.reserve", return_value=True),
        mock.patch(
            "src.utils.credit_gate.release",
            side_effect=lambda *a, **k: released.__setitem__("n", released["n"] + 1),
        ),
        mock.patch("src.utils.batch_api.submit_batch", return_value="b1"),
        mock.patch("src.utils.batch_api.poll_batch", return_value={"state": {}}),
        mock.patch(
            "src.utils.batch_api.retrieve_results_ordered",
            return_value=[BatchResult("srv", '{"ok":1}', "stop", "")],
        ),
        mock.patch("src.grok_client._save_metrics"),
    ):
        client.submit_batch(batch_name="t")
    assert released["n"] == 0, "reservation was double-released / released on SUCCESS"


# --- #2 weather processed-gate -------------------------------------------------------------
def test_weather_skips_already_processed(tmp_path):
    from src.extraction import weather_central as wc

    wdir = tmp_path / "weather"
    wdir.mkdir()
    ev = tmp_path / "ch1-event.json"
    ev.write_text("{}", encoding="utf-8")
    # mark it processed
    wc._save_weather_processed(wdir, {str(ev): True})
    with mock.patch("src.utils.config.should_reprocess", return_value=False):
        out = wc.extract_weather_central(ev, wdir, object(), tmp_path / "places")
    assert out is None  # skipped, not re-extracted


# --- #3 batch_poller partial-failure alert -------------------------------------------------
def test_partial_failure_alerts_above_threshold(monkeypatch):
    import lambda_handlers.batch_poller as bp

    alerted = {"n": 0}
    monkeypatch.setattr(
        bp,
        "_alert_partial_batch_failure",
        lambda *a: alerted.__setitem__("n", alerted["n"] + 1),
    )
    monkeypatch.setattr(bp, "_PARTIAL_FAILURE_ALERT_PCT", 10.0)

    class _R:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"state": {"num_requests": 100, "num_success": 80, "num_error": 20}}

    monkeypatch.setattr(bp.requests, "get", lambda *a, **k: _R())
    status = bp._check_batch_status("key", "batch123", submitted_at=0)
    assert status == "complete"  # proceeds on the successful fraction
    assert alerted["n"] == 1  # but ALERTED (20% > 10% threshold)


def test_partial_failure_no_alert_below_threshold(monkeypatch):
    import lambda_handlers.batch_poller as bp

    alerted = {"n": 0}
    monkeypatch.setattr(
        bp,
        "_alert_partial_batch_failure",
        lambda *a: alerted.__setitem__("n", alerted["n"] + 1),
    )
    monkeypatch.setattr(bp, "_PARTIAL_FAILURE_ALERT_PCT", 10.0)

    class _R:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"state": {"num_requests": 100, "num_success": 98, "num_error": 2}}

    monkeypatch.setattr(bp.requests, "get", lambda *a, **k: _R())
    assert bp._check_batch_status("key", "batch123", submitted_at=0) == "complete"
    assert alerted["n"] == 0  # 2% < 10% -> no alert


# --- #4 batch size cap fails loud ----------------------------------------------------------
def test_write_jsonl_fails_loud_over_request_cap(tmp_path, monkeypatch):
    from src.utils.batch_api import BatchCollector, BatchRequest, BatchSubmissionError

    monkeypatch.setenv("BATCH_MAX_REQUESTS", "2")
    coll = BatchCollector()
    for i in range(3):
        coll.add(
            BatchRequest(
                f"r{i}", [{"role": "user", "content": "p"}], "m", 0.1, "events"
            )
        )
    with pytest.raises(BatchSubmissionError):
        coll.write_jsonl(tmp_path / "b.jsonl")


def test_write_jsonl_ok_under_cap(tmp_path, monkeypatch):
    from src.utils.batch_api import BatchCollector, BatchRequest

    monkeypatch.setenv("BATCH_MAX_REQUESTS", "20000")
    coll = BatchCollector()
    coll.add(BatchRequest("r1", [{"role": "user", "content": "p"}], "m", 0.1, "events"))
    assert coll.write_jsonl(tmp_path / "b.jsonl") == 1


# --- #5 decompression-bomb rejected --------------------------------------------------------
def test_grok_search_maps_rejects_decompression_bomb(monkeypatch):
    from src.extraction import grok_search_maps as gsm
    from PIL import Image

    # a fake verify() that raises DecompressionBombError (as PIL would past MAX_IMAGE_PIXELS)
    from PIL.Image import DecompressionBombError

    class _Bomb:
        def verify(self):
            raise DecompressionBombError("bomb")

    monkeypatch.setattr(Image, "open", lambda *a, **k: _Bomb())
    ok, reason = gsm.verify_map_with_vision(
        b"\xff\xd8\xff" + b"x" * 100, "Place", None, "ctx", "Map", object()
    )
    assert ok is False  # rejected, did not crash the pipeline
