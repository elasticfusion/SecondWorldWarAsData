"""Tests for blacklist-gated source retrieval (download + human-review quarantine).

Verifies: not-strict gate (download unless blacklisted), subdomain blacklist
match, quarantine download marks retrieved_pending_review (never processed),
and non-blocking failure handling. No network — a fake session is injected.
"""

from pathlib import Path
from unittest.mock import MagicMock

from src.enrichment import source_retrieval as sr

BL = ["ebay.com", "curtiswrightmaps.com"]


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
