"""Task 1.2f: a Grok call that hangs past its wall-clock deadline is an OUTAGE, not an ordinary
error. It returns at the deadline (the old `with ThreadPoolExecutor` waited for the hung thread
on exit), is retried by the shared policy, and then stops the run as GrokUnreachable (deferred
connectivity, resumes on its own) instead of reaching a caller as a "no answer" error.
"""

import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from src.utils import grok_models as G
from tests.test_grok_models import _client


def _hanging_session(release: threading.Event):
    def post(*a, **k):
        release.wait(5)  # a hung connection (released by the test)
        return SimpleNamespace(status_code=200)

    return SimpleNamespace(post=post)


def test_a_hung_post_returns_at_the_deadline_as_a_timeout(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.timeout = 0.2
    release = threading.Event()
    started = time.monotonic()
    try:
        with pytest.raises(requests.Timeout, match="wall-clock deadline"):
            client._post_with_deadline(_hanging_session(release), {}, {})
        assert time.monotonic() - started < 2  # did not wait for the hung thread
    finally:
        release.set()


def test_repeated_deadline_misses_stop_the_run_as_a_grok_outage(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    # the shared retry policy's backoff, without its real sleeps
    monkeypatch.setattr(client._call_api_retrying.retry, "sleep", lambda s: None)
    monkeypatch.setattr(client._rate_limiter, "wait", lambda: None)
    with mock.patch.object(
        client, "_post_with_deadline", side_effect=requests.Timeout("deadline")
    ) as post:
        with pytest.raises(G.GrokUnreachable):
            client._call_api([{"role": "user", "content": "x"}], 0.1, "grok-4.3")
    assert post.call_count == 5  # retried by the shared policy first
