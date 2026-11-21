"""Task 1.2f: every pinned fetch (pages, images, Wayback, award sources, unit patches) shares ONE
retry policy in url_hygiene._pinned_get: a network error or timeout on every vetted IP, or a
429/502/503/504 answer, is retried (3 tries, Retry-After or 2 s / 4 s, capped at 30 s). Other
answers (200, 404, 403 ...) return at once; the last answer or error reaches the caller.
"""

import io
import time
from unittest import mock

import pytest
import requests

from src.utils import url_hygiene as uh


def _resp(status, retry_after=None):
    r = mock.Mock(status_code=status, headers={})
    if retry_after is not None:
        r.headers = {"Retry-After": retry_after}
    return r


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(time, "sleep", waits.append)
    return waits


def _get(monkeypatch, *answers):
    once = mock.Mock(side_effect=list(answers))
    monkeypatch.setattr(uh, "_pinned_get_once", once)
    out = uh._pinned_get("https://x.org/p", "x.org", ["1.2.3.4"], timeout=5, headers={})
    return out, once


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_a_transient_answer_is_retried(monkeypatch, slept, status):
    first, ok = _resp(status), _resp(200)
    out, once = _get(monkeypatch, first, ok)
    assert out is ok and once.call_count == 2
    first.close.assert_called_once()  # the streamed connection is freed before the retry
    assert slept == [2.0]


@pytest.mark.parametrize("status", [200, 301, 403, 404, 500])
def test_other_answers_return_at_once(monkeypatch, slept, status):
    r = _resp(status)
    out, once = _get(monkeypatch, r)
    assert out is r and once.call_count == 1 and slept == []


def test_retry_after_is_honoured_and_capped(monkeypatch, slept):
    _get(monkeypatch, _resp(429, "7"), _resp(503, "9999"), _resp(200))
    assert slept == [7.0, uh.PINNED_MAX_WAIT_S]


def test_a_bad_retry_after_uses_the_default_wait(monkeypatch, slept):
    _get(monkeypatch, _resp(429, "Wed, 21 Oct 2026 07:28:00 GMT"), _resp(200))
    assert slept == [2.0]


def test_the_last_transient_answer_reaches_the_caller(monkeypatch, slept):
    last = _resp(503)
    out, once = _get(monkeypatch, _resp(503), _resp(503), last)
    assert out is last and once.call_count == uh.PINNED_TRIES
    last.close.assert_not_called()  # the caller still reads (and closes) it


def test_network_errors_are_retried_then_raised(monkeypatch, slept):
    ok = _resp(200)
    out, once = _get(
        monkeypatch, requests.ConnectionError("x"), requests.Timeout("y"), ok
    )
    assert out is ok and slept == [2.0, 4.0]
    with pytest.raises(requests.Timeout):
        _get(monkeypatch, *[requests.Timeout("z")] * uh.PINNED_TRIES)


def test_a_non_network_error_is_not_retried(monkeypatch, slept):
    with pytest.raises(ValueError):
        _get(monkeypatch, ValueError("bad url"))
    assert slept == []


def test_a_real_error_response_has_its_retry_after_honoured(monkeypatch, slept):
    """A requests.Response is falsy for every error status (bool() is .ok): the wait must not
    test its truth value."""
    real = requests.Response()
    real.status_code = 429
    real.headers["Retry-After"] = "12"
    real.raw = io.BytesIO(b"")  # closed before the retry
    assert not real  # the trap
    _get(monkeypatch, real, _resp(200))
    assert slept == [12.0]


def test_a_stall_or_a_tls_failure_is_not_retried(monkeypatch, slept):
    clock = {"t": 0.0}

    def slow_timeout(*a, **k):
        clock["t"] += uh.PINNED_FAST_FAIL_S
        raise requests.Timeout("stalled")

    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(uh, "_pinned_get_once", slow_timeout)
    with pytest.raises(requests.Timeout):
        uh._pinned_get("https://x.org/p", "x.org", ["1.2.3.4"], timeout=5, headers={})
    with pytest.raises(requests.exceptions.SSLError):
        _get(monkeypatch, requests.exceptions.SSLError("bad cert"), _resp(200))
    assert slept == []
