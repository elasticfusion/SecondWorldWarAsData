"""Tests for blacklist-gated source retrieval (download + human-review quarantine).

Verifies: not-strict gate (download unless blacklisted), subdomain blacklist match, quarantine
download marks retrieved_pending_review (never processed), and non-blocking failure handling.

No network: the SSRF-safe fetch path is pinned (clean_url -> resolved_host_is_safe -> _pinned_get),
so tests stub resolved_host_is_safe (host vets to a public IP) and _pinned_get (returns a fake
streamed response). The host is pinned to a vetted IP, closing the DNS-rebinding TOCTOU (§1.0d).
"""

# pylint: disable=protected-access  # tests intentionally probe internal helpers

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.enrichment import source_retrieval as sr
from src.utils import url_hygiene as uh

BL = ["ebay.com", "curtiswrightmaps.com"]


@pytest.fixture(autouse=True)
def _fast_pacer(monkeypatch):
    """Neutralize module per-domain pacing so download tests don't sleep."""
    monkeypatch.setattr(sr._PACER, "wait", lambda host: None)


@pytest.fixture(autouse=True)
def _vet_host(monkeypatch):
    """By default every host vets to a single public IP (the happy path). Individual tests can
    override. _pinned_get is patched per-test to return a fake response."""
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: ["93.184.216.34"])


def _fake_resp(body=b"PDFDATA", status=200, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.iter_content = lambda **_kw: [body]
    return resp


def _patch_pinned(monkeypatch, *responses):
    """Patch _pinned_get to return the given responses in order (captures calls for assertions)."""
    calls = []
    it = iter(responses)

    def _fake_pinned(url, host, vetted, *, timeout, headers):
        calls.append({"url": url, "host": host, "vetted": vetted, "headers": headers})
        return next(it)

    monkeypatch.setattr(uh, "_pinned_get", _fake_pinned)
    return calls


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


def test_retrieved_pending_review_quarantine(tmp_path, monkeypatch):
    _patch_pinned(monkeypatch, _fake_resp())
    entry = {"resource_urls": ["https://archive.org/download/x/book.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL)
    assert res["status"] == "retrieved_pending_review"
    assert Path(res["path"]).exists()
    assert entry["retrieval_status"] == "retrieved_pending_review"
    assert entry["retrieved_path"].endswith("book.pdf")


def test_too_large_is_rejected(tmp_path, monkeypatch):
    _patch_pinned(monkeypatch, _fake_resp(body=b"x" * 1000))
    entry = {"resource_urls": ["https://archive.org/download/x/big.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL, max_bytes=100)
    assert res["status"] == "too_large"
    assert not any(tmp_path.iterdir())  # partial file cleaned up


def test_http_error_is_non_blocking(tmp_path, monkeypatch):
    _patch_pinned(monkeypatch, _fake_resp(status=404))
    entry = {"resource_urls": ["https://archive.org/download/x/missing.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL)
    assert res["status"] == "error"


def test_ssrf_blocked_host_not_downloaded(tmp_path, monkeypatch):
    """A host that resolves to a non-public IP is rejected before any fetch (SSRF gate)."""
    monkeypatch.setattr(uh, "resolved_host_is_safe", lambda host: None)
    pinned = _patch_pinned(monkeypatch, _fake_resp())
    entry = {"resource_urls": ["https://internal.evil/x.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL)
    assert res["status"] == "error" and "ssrf" in res["reason"].lower()
    assert pinned == []  # never fetched
    assert not any(tmp_path.iterdir())


def test_load_blacklist_missing_file_returns_empty(tmp_path):
    assert sr.load_blacklist(tmp_path / "nope.yaml") == []


def test_sends_browser_user_agent(tmp_path, monkeypatch):
    calls = _patch_pinned(monkeypatch, _fake_resp())
    entry = {"resource_urls": ["https://archive.org/download/x/book.pdf"]}
    sr.retrieve_source(entry, tmp_path, blacklist=BL)
    # The pinned GET must carry a browser-like User-Agent (human-like, polite).
    ua = calls[-1]["headers"]["User-Agent"]
    assert "Mozilla/5.0" in ua and "Chrome/" in ua
    # And it must be pinned to the vetted IP, with Host = original host.
    assert calls[-1]["vetted"] == ["93.184.216.34"]
    assert calls[-1]["host"] == "archive.org"


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
    r429 = _fake_resp(status=429, headers={"Retry-After": "1"})
    r200 = _fake_resp(body=b"PDF")
    calls = _patch_pinned(monkeypatch, r429, r200)
    entry = {"resource_urls": ["https://archive.org/download/x/book.pdf"]}
    res = sr.retrieve_source(entry, tmp_path, blacklist=BL)
    assert res["status"] == "retrieved_pending_review"
    assert len(calls) == 2  # retried (pinned again) after honoring Retry-After
