"""poll_batch must FAIL FAST on xAI batch cancellation / rejected submission,
not poll 0/0 for hours (root cause of the St. Vith Phase 2 loss: config model
grok-4.6 is not batch-supported -> xAI cancelled the file)."""

from unittest.mock import MagicMock, patch

import pytest

from src.utils import batch_api as ba


def _resp(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def test_failfast_on_xai_cancellation():
    payload = {
        "cancel_time": "2026-09-27",
        "cancel_by_xai_message": "JSONL file validation failed: Model grok-4.6 is not supported for batch processing.",
        "state": {
            "num_requests": 0,
            "num_pending": 0,
            "num_success": 0,
            "num_error": 0,
        },
    }
    with patch.object(ba.requests, "get", return_value=_resp(payload)):
        with pytest.raises(ba.BatchSubmissionError) as e:
            ba.poll_batch("k", "batch_x", interval=0, submitted_count=70)
    assert "not supported for batch" in str(e.value)


def test_failfast_on_submitted_but_zero_after_grace(monkeypatch):
    payload = {
        "state": {"num_requests": 0, "num_pending": 0, "num_success": 0, "num_error": 0}
    }
    monkeypatch.setattr(ba, "_SUBMIT_GRACE_SECS", 0)  # skip grace
    monkeypatch.setattr(ba.time, "sleep", lambda *_: None)
    with patch.object(ba.requests, "get", return_value=_resp(payload)):
        with pytest.raises(ba.BatchSubmissionError):
            ba.poll_batch("k", "batch_x", interval=0, submitted_count=70)


def test_normal_completion_still_returns():
    payload = {
        "state": {"num_requests": 5, "num_pending": 0, "num_success": 5, "num_error": 0}
    }
    with patch.object(ba.requests, "get", return_value=_resp(payload)):
        out = ba.poll_batch("k", "batch_x", interval=0, submitted_count=5)
    assert out["state"]["num_success"] == 5
