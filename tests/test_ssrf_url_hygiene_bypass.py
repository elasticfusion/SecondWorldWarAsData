"""SSRF regression — §1.0b re-review 'url_hygiene bypass codebase-wide'.

Each site below previously fetched an UNTRUSTED URL (web-search / LLM / entry-derived) with a bare
session.get(allow_redirects=True) — an SSRF hole. They now route through url_hygiene (safe_get /
fetch_text_safe / clean_url+resolved_host_is_safe). These tests assert an SSRF-blocked host is
refused (safe/empty result, no raw fetch) and the LIVE bibliography verifier REJECTS a blocked URL.
"""

from unittest import mock


# --- LIVE: bibliography_resolver._verify_url_content --------------------------------------
def test_bib_verify_url_rejects_ssrf_blocked_url_without_fetch():
    from src.enrichment import bibliography_resolver as br

    grok = mock.Mock()
    # loopback/metadata URL -> clean_url returns None -> hard REJECT, grok never consulted.
    for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1/x"):
        assert br._verify_url_content(url, "some citation", grok) is False, url
    grok.chat_completion.assert_not_called()


def test_bib_verify_url_rejects_host_resolving_private(monkeypatch):
    from src.enrichment import bibliography_resolver as br

    # public-looking host that resolves private -> fetch_text_safe returns (None, None) -> reject.
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: None
    )
    grok = mock.Mock()
    with mock.patch("requests.get") as g:
        assert br._verify_url_content("https://evil.example.org/x", "c", grok) is False
        g.assert_not_called()


# --- equipment._download_media (image) ----------------------------------------------------
def test_equipment_download_media_blocks_loopback():
    from src.extraction import equipment

    with mock.patch("src.utils.http_pool.get_session") as sess:
        out = equipment._download_media_file(
            {"url": "http://127.0.0.1/evil.jpg"},
            "Tiger",
            "tank",
            __import__("pathlib").Path("/tmp/x"),
            None,
        )
        assert out is None
        sess.return_value.get.assert_not_called()  # never hit the raw session


# --- equipment._extract_image_urls_from_page (html) ---------------------------------------
def test_equipment_extract_image_urls_blocks_loopback():
    from src.extraction import equipment

    out = equipment._extract_image_urls_from_page(
        "http://169.254.169.254/x", "Tiger", mock.Mock()
    )
    assert out == []


# --- source_retrieval._download_to_quarantine --------------------------------------------
def test_source_retrieval_download_blocks_loopback():
    from src.enrichment import source_retrieval as sr
    from src.utils import url_hygiene as uh

    # _download_to_quarantine now PINS the fetch (no injected session); a loopback host must be
    # rejected by clean_url/resolved_host_is_safe BEFORE any _pinned_get call.
    pinned = mock.Mock()
    with mock.patch.object(uh, "_pinned_get", pinned):
        res = sr._download_to_quarantine(
            {}, "http://127.0.0.1/secret", __import__("pathlib").Path("/tmp/q"), 1024
        )
    assert res["status"] == "error" and "ssrf" in res["reason"]
    pinned.assert_not_called()


# --- award_polite_source fetch ------------------------------------------------------------
def test_award_polite_source_blocks_loopback(tmp_path):
    from src.enrichment import award_polite_source as aps

    src = aps.PoliteSource.__new__(aps.PoliteSource)
    src._cache = tmp_path
    src._source_id = "x"
    src._crawl_delay = 0
    src.name = "x"
    src._session = mock.Mock()
    src.last_error = None
    out = src.polite_get("http://127.0.0.1/x")
    assert out is None
    assert src.last_error and "ssrf" in src.last_error
    src._session.get.assert_not_called()


# --- url_extractor.fetch_url --------------------------------------------------------------
def test_url_extractor_fetch_url_blocks_loopback(tmp_path):
    import pytest
    from src.url_extractor import URLExtractor

    ex = URLExtractor(tmp_path)
    with pytest.raises(ValueError):
        ex.fetch_url("http://169.254.169.254/x")


# --- enrich_biographies._fetch_url_content (live source-url fetch; found in review) ------
def test_bio_fetch_url_content_blocks_loopback_then_wayback(monkeypatch):
    from src.extraction import enrich_biographies as eb

    # the UNTRUSTED live url is SSRF-blocked -> no raw requests.get; falls through to wayback
    # (archive.org, trusted). Stub wayback to None so we assert the live path was refused.
    monkeypatch.setattr("src.enrichment.wayback.fetch_archived", lambda *a, **k: None)
    with mock.patch("requests.get") as g:
        out = eb._fetch_url_content("http://169.254.169.254/latest/meta-data/")
        assert out is None
        g.assert_not_called()  # live fetch routed via fetch_text_safe, blocked pre-request


# --- static guard: the newly-routed modules carry no bare unguarded requests.get ----------
def test_newly_routed_modules_have_no_unguarded_requests_get():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    files = [
        root / "src" / "url_extractor.py",
        root / "src" / "extraction" / "enrich_biographies.py",  # _preserve_person_photo
    ]
    offenders = []
    for f in files:
        txt = f.read_text()
        # _preserve_person_photo was the only untrusted-host GET in enrich_biographies routed here;
        # assert the specific portrait-fetch no longer uses get_session().get on img_url.
        if (
            "get_session().get(\n        img_url" in txt
            or "get_session().get(img_url" in txt
        ):
            offenders.append(f"{f.name}: get_session().get(img_url ...)")
    assert not offenders, "; ".join(offenders)
