"""Tests for the Wayback Machine WAF-fallback (archive.org), fully offline."""

from unittest.mock import MagicMock, patch

from src.enrichment import wayback

# ---- block-page detection ----


def test_detects_incapsula_200_block():
    body = (
        "Request unsuccessful. Incapsula incident ID: 1021-5872 "
        '<script src="/_Incapsula_Resource?SWJIYLWA"></script>'
    )
    assert wayback.is_block_page(body, 200) is True


def test_detects_hard_status_blocks():
    assert wayback.is_block_page("x", 403) is True
    assert wayback.is_block_page("x", 429) is True
    assert wayback.is_block_page("x", 503) is True


def test_real_article_not_flagged():
    article = "Cloudflare is a CDN. " + ("Clarke led the 7th Armored. " * 300)
    assert wayback.is_block_page(article, 200) is False


def test_empty_not_flagged():
    assert wayback.is_block_page("", 200) is False
    assert wayback.is_block_page(None, 200) is False


# ---- polite fetch: Retry-After honored ----


def test_retry_after_is_honored(monkeypatch):
    monkeypatch.setattr(wayback.time, "sleep", lambda *_: None)  # no real waiting
    throttled = MagicMock(status_code=503, headers={"Retry-After": "1"})
    ok = MagicMock(status_code=200, headers={})
    with patch.object(wayback.requests, "get", side_effect=[throttled, ok]) as g:
        resp = wayback._get_with_courtesy("https://archive.org/x", timeout=5)
    assert resp is ok
    assert g.call_count == 2  # retried once after the 503


def test_identifying_user_agent_sent(monkeypatch):
    monkeypatch.setattr(wayback.time, "sleep", lambda *_: None)
    captured = {}

    def fake_get(url, headers=None, timeout=None, **kw):
        captured["ua"] = (headers or {}).get("User-Agent", "")
        return MagicMock(status_code=200, headers={})

    with patch.object(wayback.requests, "get", side_effect=fake_get):
        wayback._get_with_courtesy("https://archive.org/x", timeout=5)
    assert "SecondWorldWarAsData" in captured["ua"]  # not a spoofed browser UA


# ---- snapshot lookup + archived fetch ----


def _availability(found: bool):
    r = MagicMock(status_code=200)
    r.json.return_value = (
        {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "https://web.archive.org/web/20230101/https://x.com",
                    "timestamp": "20230101000000",
                }
            }
        }
        if found
        else {"archived_snapshots": {}}
    )
    return r


def test_fetch_archived_returns_content_and_provenance(monkeypatch):
    monkeypatch.setattr(wayback.time, "sleep", lambda *_: None)
    avail = _availability(True)
    page = MagicMock(status_code=200, headers={})
    page.text = "<html><body>Real archived biography content.</body></html>"
    with patch.object(wayback.requests, "get", side_effect=[avail, page]):
        out = wayback.fetch_archived("https://x.com", timeout=5)
    assert out is not None
    html, archived_url, ts = out
    assert "biography" in html
    assert archived_url.startswith("https://web.archive.org/")
    assert ts == "20230101000000"


def test_fetch_archived_none_when_no_snapshot(monkeypatch):
    monkeypatch.setattr(wayback.time, "sleep", lambda *_: None)
    with patch.object(wayback.requests, "get", return_value=_availability(False)):
        assert wayback.fetch_archived("https://x.com", timeout=5) is None


def test_archived_snapshot_itself_a_block_page_rejected(monkeypatch):
    monkeypatch.setattr(wayback.time, "sleep", lambda *_: None)
    avail = _availability(True)
    blocked = MagicMock(status_code=200, headers={})
    blocked.text = "Request unsuccessful. Incapsula incident ID: 1"
    with patch.object(wayback.requests, "get", side_effect=[avail, blocked]):
        assert wayback.fetch_archived("https://x.com", timeout=5) is None


# ---- integration: _fetch_url_content transparent fallback + provenance ----


def test_fetch_url_content_falls_back_on_block(monkeypatch):
    from src.extraction import enrich_biographies as eb

    # Live fetch returns an Incapsula block page (HTTP 200).
    blocked = MagicMock(status_code=200)
    blocked.text = "Request unsuccessful. Incapsula incident ID: 42"
    monkeypatch.setattr(eb.requests, "get", lambda *a, **k: blocked)
    # Wayback recovers real content.
    monkeypatch.setattr(
        wayback,
        "fetch_archived",
        lambda url, timeout=20: (
            "<html>Recovered biography.</html>",
            "https://web.archive.org/web/20230101/https://x.com",
            "20230101000000",
        ),
    )
    prov: dict = {}
    html = eb._fetch_url_content("https://x.com", provenance=prov)
    assert html is not None and "Recovered" in html
    assert prov["source"] == "wayback"
    assert prov["wayback_capture_timestamp"] == "20230101000000"


def test_fetch_url_content_live_ok_no_fallback(monkeypatch):
    from src.extraction import enrich_biographies as eb

    good = MagicMock(status_code=200)
    good.text = "<html>" + ("Real content. " * 400) + "</html>"
    monkeypatch.setattr(eb.requests, "get", lambda *a, **k: good)
    # If this were called, the test would fail (we assert it is not needed).
    called = {"n": 0}

    def _should_not_run(*a, **k):
        called["n"] += 1
        return None

    monkeypatch.setattr(wayback, "fetch_archived", _should_not_run)
    prov: dict = {}
    html = eb._fetch_url_content("https://x.com", provenance=prov)
    assert "Real content" in html
    assert called["n"] == 0  # live content used; no archive call
    assert prov == {}  # no provenance stamp on a live fetch


# ---- bibliographical provenance on the stored result ----


def test_process_positive_url_records_archive_provenance(monkeypatch):
    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr(oe, "_url_verdict_cached", lambda u: None)
    monkeypatch.setattr(oe, "_verify_result", lambda *a, **k: True)
    monkeypatch.setattr(oe, "_cache_url_verdict", lambda *a, **k: None)

    def fake_sum(url, ctx, gc, provenance=None):
        if provenance is not None:
            provenance["archived_url"] = (
                "https://web.archive.org/web/20230101000000/https://x.com"
            )
            provenance["wayback_capture_timestamp"] = "20230101000000"
        return "Recovered biography."

    monkeypatch.setattr(oe, "_summarize_url_page", fake_sum)
    r = oe.process_positive_url(
        "https://x.com", "T", "snippet", "ctx", grok_client=object()
    )
    assert r is not None
    # Original source URL preserved as the citation anchor.
    assert r["url"] == "https://x.com"
    # Archive retrieval recorded alongside it.
    assert r["retrieved_from"] == "wayback"
    assert r["archived_url"].startswith("https://web.archive.org/")
    # Archive capture date AND our retrieval date/time both present.
    assert r["archive_capture_date"] == "2023-01-01T00:00:00+00:00"
    assert r["wayback_capture_timestamp"] == "20230101000000"
    assert "retrieved_at" in r and r["retrieved_at"].endswith("+00:00")


def test_process_positive_url_no_archive_fields_on_live(monkeypatch):
    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr(oe, "_url_verdict_cached", lambda u: None)
    monkeypatch.setattr(oe, "_verify_result", lambda *a, **k: True)
    monkeypatch.setattr(oe, "_cache_url_verdict", lambda *a, **k: None)
    # Live summarize fills no provenance.
    monkeypatch.setattr(
        oe, "_summarize_url_page", lambda u, c, g, provenance=None: "Live summary."
    )
    r = oe.process_positive_url(
        "https://x.com", "T", "snippet", "ctx", grok_client=object()
    )
    assert r is not None and "archived_url" not in r and "retrieved_from" not in r


def test_wayback_ts_to_iso():
    import src.enrichment.openserp_enrichment as oe

    assert oe._wayback_ts_to_iso("20230101120000") == "2023-01-01T12:00:00+00:00"
    assert oe._wayback_ts_to_iso("") == ""
    assert oe._wayback_ts_to_iso("bad") == ""
