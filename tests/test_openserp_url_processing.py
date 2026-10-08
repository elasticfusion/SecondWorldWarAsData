"""Tests for OpenSERP positive-URL processing: fetch+summarize + positive/negative URL cache."""

from unittest.mock import MagicMock, patch

import pytest

import src.enrichment.openserp_enrichment as oe


class _Backend:
    """In-memory stand-in for the search-cache backend."""

    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def put(self, k, v, ttl_days=30):
        self.store[k] = v


@pytest.fixture
def backend():
    b = _Backend()
    with (
        patch("src.utils.search_cache._get_backend", return_value=b),
        patch("src.utils.search_cache._make_key", side_effect=lambda s, q: f"{s}:{q}"),
    ):
        yield b


def test_positive_url_fetched_summarized_and_cached(backend):
    gc = MagicMock()
    gc.chat_completion.return_value = "Summary of the page."
    with (
        patch.object(oe, "_verify_result", return_value=True),
        patch(
            "src.extraction.enrich_biographies._fetch_url_content",
            return_value="<p>Content.</p>",
        ),
    ):
        r = oe.process_positive_url("http://good", "T", "snip", "ctx", gc)
    assert r["url"] == "http://good"
    assert r["summary"] == "Summary of the page."
    assert "fetched_at" in r
    assert backend.store["openserp_url_verdict:http://good"] == "Summary of the page."


def test_positive_rerun_served_from_cache_no_refetch(backend):
    backend.store["openserp_url_verdict:http://good"] = "Cached summary."
    gc = MagicMock()
    with (
        patch("src.extraction.enrich_biographies._fetch_url_content") as fetch,
        patch.object(oe, "_verify_result") as verify,
    ):
        r = oe.process_positive_url("http://good", "T", "snip", "ctx", gc)
    assert r["summary"] == "Cached summary."
    assert not fetch.called  # no re-fetch within retention
    assert not verify.called  # no re-verify


def test_rejected_url_is_negative_cached(backend):
    gc = MagicMock()
    with patch.object(oe, "_verify_result", return_value=False):
        r = oe.process_positive_url("http://bad", "T", "snip", "ctx", gc)
    assert r is None
    assert backend.store["openserp_url_verdict:http://bad"] == "REJECT"


def test_rejected_rerun_skips_reprocessing(backend):
    backend.store["openserp_url_verdict:http://bad"] = "REJECT"
    gc = MagicMock()
    with patch.object(oe, "_verify_result") as verify:
        r = oe.process_positive_url("http://bad", "T", "snip", "ctx", gc)
    assert r is None
    assert not verify.called  # negative cache prevents reprocessing


def test_dead_page_after_verify_is_negative_cached(backend):
    gc = MagicMock()
    with (
        patch.object(oe, "_verify_result", return_value=True),
        patch(
            "src.extraction.enrich_biographies._fetch_url_content", return_value=None
        ),
    ):
        r = oe.process_positive_url("http://dead", "T", "snip", "ctx", gc)
    assert r is None  # verified but unfetchable -> no result
    assert backend.store["openserp_url_verdict:http://dead"] == "REJECT"
