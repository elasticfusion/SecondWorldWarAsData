"""Tests for wiring the credit gate into batch submission (M4.4, spec §9.0).

Verifies:
- _estimate_batch_cost sums per-request cost using per-kind model routing.
- submit_batch HOLDS (raises BatchSubmissionError) when the credit gate refuses,
  and does NOT call the low-level submit_batch.
- A reservation is released if the low-level submission itself fails.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("GROK_API_KEY", "test-key")

import pytest

from src.grok_client import GrokClient
from src.utils.batch_api import BatchSubmissionError


def _client(tmp_path):
    c = GrokClient(cache_dir=tmp_path, batch_mode=True)
    c._config = {"cost": {"credit_gating": True, "budget_usd": 10}}
    return c


def _fake_request(content, cache_type):
    r = MagicMock()
    r.messages = [{"role": "user", "content": content}]
    r.cache_type = cache_type
    r.request_id = f"req-{cache_type}"
    return r


def test_estimate_batch_cost_sums_per_request(tmp_path):
    c = _client(tmp_path)
    c._model_map = {}  # everything -> default model
    c.model = "grok-4.20-0309-reasoning"
    collector = MagicMock()
    collector.requests = [
        _fake_request("x" * 4000, "events"),  # ~1000 input tokens
        _fake_request("y" * 4000, "people"),
    ]
    c._batch_collector = collector
    cost = c._estimate_batch_cost()
    assert cost > 0  # both requests priced


def test_estimate_zero_when_no_collector(tmp_path):
    c = _client(tmp_path)
    c._batch_collector = None
    assert c._estimate_batch_cost() == 0.0


def test_submit_holds_when_gate_refuses(tmp_path):
    c = _client(tmp_path)
    collector = MagicMock()
    collector.requests = [_fake_request("z" * 100, "events")]
    collector.__len__ = lambda self: 1
    collector.write_jsonl = MagicMock(return_value=1)
    c._batch_collector = collector

    with patch("src.utils.credit_gate.reserve", return_value=False):
        with patch("src.utils.batch_api.submit_batch") as low_submit:
            with pytest.raises(BatchSubmissionError, match="Credit gate"):
                c.submit_batch(batch_name="test")
            low_submit.assert_not_called()  # must NOT submit held work


def test_submit_releases_reservation_on_submit_failure(tmp_path):
    c = _client(tmp_path)
    collector = MagicMock()
    collector.requests = [_fake_request("z" * 100, "events")]
    collector.__len__ = lambda self: 1
    collector.write_jsonl = MagicMock(return_value=1)
    c._batch_collector = collector

    with patch("src.utils.credit_gate.reserve", return_value=True):
        with patch("src.utils.credit_gate.release") as rel:
            with patch(
                "src.utils.batch_api.submit_batch",
                side_effect=RuntimeError("upload failed"),
            ):
                with pytest.raises(RuntimeError, match="upload failed"):
                    c.submit_batch(batch_name="test")
                rel.assert_called_once()  # reservation rolled back
