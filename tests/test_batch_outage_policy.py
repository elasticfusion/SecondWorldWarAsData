"""Owner 2026-11-20 (task 1.2f): batches use the shared outage policy. Every xAI Batch API call
(upload, create, poll, retrieve; the batch poller's status check) goes through
batch_api.xai_request: a network error, timeout, 429 or 5xx is retried, then the run stops as
GrokUnreachable (402: GrokFundsExhausted) - never an ordinary error counted as a failed batch.
A batch already submitted when xAI goes away is kept for the poller, never marked failed.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from src.utils import batch_api as ba
from src.utils import grok_models as G


def _resp(status, body=None, retry_after=None):
    r = mock.Mock(status_code=status, headers={})
    if retry_after is not None:
        r.headers = {"Retry-After": retry_after}
    r.json.return_value = body or {}
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return r


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(ba.time, "sleep", waits.append)
    return waits


def _call(monkeypatch, *answers, tries=ba.XAI_TRIES):
    req = mock.Mock(side_effect=list(answers))
    monkeypatch.setattr(ba.requests, "request", req)
    return (
        ba.xai_request("GET", "/batches/b", "k", what="poll", timeout=5, tries=tries),
        req,
    )


def test_a_transient_answer_is_retried(monkeypatch, slept):
    ok = _resp(200, {"id": 1})
    out, req = _call(monkeypatch, _resp(503), requests.ConnectionError("x"), ok)
    assert out is ok and req.call_count == 3 and slept == [4.0, 8.0]
    assert req.call_args.kwargs["headers"]["Authorization"] == "Bearer k"


def test_retry_after_is_honoured_and_capped(monkeypatch, slept):
    _call(
        monkeypatch,
        _resp(429, retry_after="7"),
        _resp(429, retry_after="900"),
        _resp(200),
    )
    assert slept == [7.0, ba.XAI_MAX_WAIT_S]


@pytest.mark.parametrize(
    "answer", [_resp(503), requests.Timeout("t"), requests.ConnectionError("c")]
)
def test_a_lasting_outage_stops_the_run(monkeypatch, slept, answer):
    with pytest.raises(G.GrokUnreachable):
        _call(monkeypatch, *[answer] * ba.XAI_TRIES)


def test_no_credit_stops_the_run_at_once(monkeypatch, slept):
    with pytest.raises(G.GrokFundsExhausted):
        _call(monkeypatch, _resp(402))
    assert slept == []


def test_a_client_error_is_an_ordinary_error(monkeypatch, slept):
    with pytest.raises(requests.HTTPError):
        _call(monkeypatch, _resp(400))
    assert slept == []


def test_submit_sends_the_same_bytes_on_a_retry(monkeypatch, slept, tmp_path):
    f = tmp_path / "b.jsonl"
    f.write_text('{"x": 1}\n')
    req = mock.Mock(
        side_effect=[
            _resp(503),
            _resp(200, {"id": "file1"}),
            _resp(200, {"batch_id": "B1"}),
        ]
    )
    monkeypatch.setattr(ba.requests, "request", req)
    assert ba.submit_batch("k", f, "n") == "B1"
    uploads = [c for c in req.call_args_list if c.args[1].endswith("/files")]
    assert len(uploads) == 2 and all(
        c.kwargs["files"]["file"] == ("b.jsonl", b'{"x": 1}\n') for c in uploads
    )


def test_retrieve_goes_through_the_policy(monkeypatch, slept):
    page = {"results": [{"batch_request_id": "r1", "batch_result": {"response": {
        "chat_get_completion": {"choices": [{"message": {"content": "ok"}}]}}}}]}  # fmt: skip
    req = mock.Mock(side_effect=[_resp(502), _resp(200, page)])
    monkeypatch.setattr(ba.requests, "request", req)
    assert ba.retrieve_results("k", "B1")["r1"].content == "ok"


def test_the_poller_keeps_a_batch_pending_when_xai_is_down(monkeypatch, slept, caplog):
    import lambda_handlers.batch_poller as BP

    monkeypatch.setattr(ba.requests, "request", mock.Mock(side_effect=[_resp(503)] * 9))
    assert BP._check_batch_status("k", "B1") == "unavailable"
    assert "batch.xai_unavailable" in caplog.text
    assert len(slept) == BP.POLL_TRIES - 1  # short: the Lambda polls again soon
    assert "transient" not in G._stopped  # no process-wide stop in a warm Lambda


def test_the_poller_stops_polling_the_rest_while_xai_is_away(monkeypatch):
    import lambda_handlers.batch_poller as BP

    checks = []
    monkeypatch.setattr(
        BP, "_check_batch_status", lambda *a: checks.append(a[1]) or "unavailable"
    )
    monkeypatch.setattr(BP, "_get_api_key", lambda: "k")
    monkeypatch.setattr(
        BP,
        "_get_pending_jobs",
        lambda: [{"batch_id": b, "status": "pending"} for b in ("b1", "b2", "b3")],
    )
    out = BP.handler({}, None)
    assert checks == ["b1"] and out["pending"] == 3


def test_an_in_task_poll_rides_out_a_few_minutes_of_errors(monkeypatch, slept):
    ok = _resp(200, {"state": {}})
    req = mock.Mock(side_effect=[_resp(502)] * (ba.POLL_TRIES - 1) + [ok])
    monkeypatch.setattr(ba.requests, "request", req)
    assert ba.get_batch("k", "B1") == {"state": {}}
    assert sum(slept) >= 200  # ~4 minutes, as the old poll loop tolerated


def test_a_broken_body_is_retried_like_a_network_error(monkeypatch, slept):
    ok = _resp(200)
    out, _ = _call(monkeypatch, requests.exceptions.ChunkedEncodingError("cut"), ok)
    assert out is ok


@pytest.mark.parametrize(
    "first", [requests.ReadTimeout("t"), _resp(500), requests.ConnectionError("reset")]
)
def test_a_batch_is_never_created_twice(monkeypatch, slept, tmp_path, first):
    """POST /batches is not idempotent: a reply that may have come after xAI created the batch
    is not retried (a second, billed batch); the operator is told to check before re-running.
    """
    f = tmp_path / "b.jsonl"
    f.write_text("{}\n")
    alerts = []
    monkeypatch.setattr(
        "src.utils.observe.observe_error", lambda s, e, **k: alerts.append(e)
    )
    req = mock.Mock(side_effect=[_resp(200, {"id": "file1"}), first, _resp(200)])
    monkeypatch.setattr(ba.requests, "request", req)
    with pytest.raises(G.GrokUnreachable):
        ba.submit_batch("k", f, "phase2-B-9reqs")
    creates = [c for c in req.call_args_list if c.args[1].endswith("/batches")]
    assert len(creates) == 1 and alerts == ["create_uncertain"]


@pytest.mark.parametrize(
    "first", [_resp(429), _resp(503), requests.ConnectTimeout("never sent")]
)
def test_a_create_that_never_happened_is_retried(monkeypatch, slept, tmp_path, first):
    f = tmp_path / "b.jsonl"
    f.write_text("{}\n")
    req = mock.Mock(
        side_effect=[_resp(200, {"id": "file1"}), first, _resp(200, {"batch_id": "B1"})]
    )
    monkeypatch.setattr(ba.requests, "request", req)
    assert ba.submit_batch("k", f, "n") == "B1"


def _client_with_batch(monkeypatch, tmp_path, poll_error):
    import src.grok_client as gc
    from src.utils import credit_gate
    from src.utils.cache_backend import DiskCacheBackend

    monkeypatch.setenv("GROK_API_KEY", "test-not-a-key")
    client = gc.GrokClient(DiskCacheBackend(tmp_path))
    monkeypatch.setattr(client, "_record_batch_submitted", lambda *a: None)
    monkeypatch.setattr(client, "_record_in_flight", lambda *a: None)
    released, marked = [], []
    monkeypatch.setattr(credit_gate, "release", lambda *a, **k: released.append(a))
    monkeypatch.setattr("src.utils.job_queue.mark_in_task", lambda *a: marked.append(a))
    monkeypatch.setattr(ba, "submit_batch", lambda *a: "B1")
    monkeypatch.setattr(ba, "poll_batch", mock.Mock(side_effect=poll_error))
    return client, released, marked


def test_an_outage_after_the_submit_hands_the_batch_to_the_poller(
    monkeypatch, tmp_path
):
    """The batch lives on at xAI and is billed: it is handed off like an unanswered batch
    (BatchPending: the phase exits HANDED_OFF and the poller retrieves it), never resubmitted.
    """
    client, released, marked = _client_with_batch(
        monkeypatch, tmp_path, G.GrokUnreachable("down")
    )
    saved = []
    monkeypatch.setattr("src.grok_client._save_metrics", saved.append)
    (tmp_path / "x.jsonl").write_text("{}")
    with pytest.raises(ba.BatchPending) as pending:
        client._submit_poll_retrieve(tmp_path / "x.jsonl", "n", 3, 0.5, 10)
    assert pending.value.batch_id == "B1" and saved[0].batch_id == "B1"
    assert released == [] and marked == []  # the reservation stays: still billed


def test_a_missing_model_still_marks_the_batch_failed(monkeypatch, tmp_path):
    client, released, marked = _client_with_batch(
        monkeypatch, tmp_path, G.GrokModelUnavailable("gone")
    )
    with pytest.raises(G.GrokModelUnavailable):
        client._submit_poll_retrieve(tmp_path / "x.jsonl", "n", 3, 0.5, 10)
    assert released and marked == [("B1", "failed")]


@pytest.mark.parametrize("first", [_resp(400), _resp(402)])
def test_a_refused_create_is_not_reported_as_uncertain(
    monkeypatch, slept, tmp_path, first
):
    f = tmp_path / "b.jsonl"
    f.write_text("{}\n")
    alerts = []
    monkeypatch.setattr(
        "src.utils.observe.observe_error", lambda s, e, **k: alerts.append(e)
    )
    req = mock.Mock(side_effect=[_resp(200, {"id": "file1"}), first])
    monkeypatch.setattr(ba.requests, "request", req)
    with pytest.raises((requests.HTTPError, G.GrokFundsExhausted)):
        ba.submit_batch("k", f, "n")
    assert alerts == []
