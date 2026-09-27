"""Tests for blacklist-gated source retrieval (download + human-review quarantine).

Verifies: not-strict gate (download unless blacklisted), subdomain blacklist
match, quarantine download marks retrieved_pending_review (never processed),
and non-blocking failure handling. No network — a fake session is injected.
"""

# pylint: disable=protected-access  # tests intentionally probe internal helpers

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.enrichment import source_retrieval as sr

BL = ["ebay.com", "curtiswrightmaps.com"]


@pytest.fixture(autouse=True)
def _fast_pacer(monkeypatch):
    """Neutralize module per-domain pacing so download tests don't sleep."""
    monkeypatch.setattr(sr._PACER, "wait", lambda host: None)


def test_is_blacklisted_exact_and_subdomain():
    assert sr.is_blacklisted("https://ebay.com/x", BL) is True
    assert sr.is_blacklisted("https://www.ebay.com/x", BL) is True  # subdomain
    assert sr.is_blacklisted("https://archive.org/details/x", BL) is False


def test_no_url_returns_no_url(tmp_path):
    res = sr.retrieve_source({}, tmp_path / "none", blacklist=BL)
    assert res["status"] == "no_url"


def test_blacklisted_not_downloaded(tmp_path):
    entry = {"resource_urls": ["https://ebay.com/item/123.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL)
    assert res["status"] == "blacklisted"
    assert entry.get("retrieval_status") == "blacklisted"
    assert not any(tmp_path.iterdir())  # nothing written


def _fake_session(body=b"PDFDATA", status=200):
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.iter_content = lambda **_kw: [body]
    sess.get.return_value = resp
    return sess


def test_retrieved_pending_review_quarantine(tmp_path):
    entry = {"resource_urls": ["https://archive.org/download/x/book.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL, session=_fake_session())
    assert res["status"] == "retrieved_pending_review"
    # File landed in the quarantine dir, entry marked pending review (NOT processed).
    assert Path(res["path"]).exists()
    assert entry["retrieval_status"] == "retrieved_pending_review"
    assert entry["retrieved_path"].endswith("book.pdf")


def test_too_large_is_rejected(tmp_path):
    entry = {"resource_urls": ["https://archive.org/download/x/big.pdf"]}
    res = sr.retrieve_source(
        entry,
        tmp_path,
        blacklist=BL,
        session=_fake_session(body=b"x" * 1000),
        max_bytes=100,
    )
    assert res["status"] == "too_large"
    assert not any(tmp_path.iterdir())  # partial file cleaned up


def test_http_error_is_non_blocking(tmp_path):
    entry = {"resource_urls": ["https://archive.org/download/x/missing.pdf"]}
    res = sr.retrieve_source(
        entry, tmp_path, blacklist=BL, session=_fake_session(status=404)
    )
    assert res["status"] == "error"


def test_load_blacklist_missing_file_returns_empty(tmp_path):
    assert sr.load_blacklist(tmp_path / "nope.yaml") == []


def test_sends_browser_user_agent(tmp_path):
    sess = _fake_session()
    entry = {"resource_urls": ["https://archive.org/download/x/book.pdf"]}
    sr.retrieve_source(entry, tmp_path, blacklist=BL, session=sess)
    # The GET must carry a browser-like User-Agent (human-like, polite).
    _, kwargs = sess.get.call_args
    ua = kwargs["headers"]["User-Agent"]
    assert "Mozilla/5.0" in ua and "Chrome/" in ua


def test_parse_retry_after():
    assert sr._parse_retry_after("30") == 30.0
    assert sr._parse_retry_after(None) == 10.0  # default
    assert sr._parse_retry_after("garbage") == 10.0
    assert sr._parse_retry_after("9999") == 120.0  # capped


def test_pacer_spaces_same_domain(monkeypatch):
    slept = []
    monkeypatch.setattr(sr.time, "sleep", slept.append)
    pacer = sr._DomainPacer(min_interval=5.0, jitter=0.0)
    pacer.wait("archive.org")  # first: no wait
    pacer.wait("archive.org")  # second: must wait ~5s
    assert slept and slept[-1] > 0


def test_429_then_retry_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(sr.time, "sleep", lambda _s: None)  # don't actually sleep
    sess = MagicMock()
    r429 = MagicMock()
    r429.status_code = 429
    r429.headers = {"Retry-After": "1"}
    r200 = MagicMock()
    r200.status_code = 200
    r200.iter_content = lambda **_kw: [b"PDF"]
    sess.get.side_effect = [r429, r200]
    entry = {"resource_urls": ["https://archive.org/download/x/book.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL, session=sess)
    assert res["status"] == "retrieved_pending_review"
    assert sess.get.call_count == 2  # retried after honoring Retry-After
