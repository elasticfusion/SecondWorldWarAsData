"""SSRF regression — Phase-2 review PATTERN 2.

The pipeline fetches URLs sourced from scraped HTML / search results / LLM output /
source-document metadata. All such GETs MUST route through the SSRF-safe helpers
(url_hygiene.safe_get / fetch_text_safe, or images.fetch_image_bytes) which: reject
non-http(s) + loopback/private/link-local/metadata hosts, re-resolve the host (DNS
rebinding), DISABLE redirects, and cap the body — never a bare requests.get(allow_redirects=True).
"""

import pathlib
from unittest import mock

from src.utils.url_hygiene import safe_get, fetch_text_safe


# --- safe_get / fetch_text_safe guards -------------------------------------------
def test_safe_get_blocks_non_http_scheme():
    assert safe_get("file:///etc/passwd") is None
    assert safe_get("javascript:alert(1)") is None
    assert fetch_text_safe("data:text/html,x") == (None, None)


def test_safe_get_blocks_loopback_and_metadata_hosts():
    # clean_url rejects these at the host-string / IP-literal level — no request made.
    for url in (
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/x",
        "http://10.0.0.5/internal",
        "http://[::1]/x",
        "http://2130706433/x",  # decimal 127.0.0.1
    ):
        assert safe_get(url) is None, url


def test_safe_get_blocks_host_resolving_to_private_ip(monkeypatch):
    # public-looking hostname that DNS resolves to a private IP -> blocked, no fetch.
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: None
    )
    with mock.patch("requests.get") as g:
        assert safe_get("https://evil.example.org/x") is None
        g.assert_not_called()


def test_safe_get_disables_redirects_and_caps_body(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 200
    resp.iter_content = lambda n: [b"x" * 1000, b"y" * 1000]
    with mock.patch("requests.get", return_value=resp) as g:
        out = safe_get("https://ex.org/page", max_bytes=1500)
        assert out is not None
        # redirects MUST be disabled (a redirect reopens SSRF)
        assert g.call_args.kwargs.get("allow_redirects") is False
        # body capped at max_bytes boundary (stops after exceeding 1500)
        assert len(out._content) <= 2000


def test_fetch_text_safe_returns_text_and_status(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 200
    resp.iter_content = lambda n: [b"<html>hi</html>"]
    resp.text = "<html>hi</html>"
    with mock.patch("requests.get", return_value=resp):
        text, status = fetch_text_safe("https://ex.org/page")
        assert status == 200 and "hi" in text


def test_fetch_text_safe_non_200_returns_status_no_text(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 404
    resp.iter_content = lambda n: [b""]
    with mock.patch("requests.get", return_value=resp):
        text, status = fetch_text_safe("https://ex.org/missing")
        assert text is None and status == 404


def test_safe_get_network_error_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    with mock.patch("requests.get", side_effect=OSError("boom")):
        assert safe_get("https://ex.org/x") is None  # swallowed


# --- routing guard: the cluster modules carry no bare unguarded requests.get ------
def test_safe_get_materializes_real_body_for_text(monkeypatch):
    """Feed bytes through iter_content on a real requests.Response and assert the materialized
    _content decodes via .text (locks in the resp._content = body path, not a mocked .text).
    """
    import requests as _requests

    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    real = _requests.Response()
    real.status_code = 200
    real.headers["Content-Type"] = "text/html; charset=utf-8"
    real.iter_content = lambda n: [b"<html>", "caf\u00e9".encode("utf-8"), b"</html>"]
    with mock.patch("requests.get", return_value=real):
        out = safe_get("https://ex.org/p")
        assert out is not None
        assert out.text == "<html>café</html>"  # decoded from the capped _content


def test_ssrf_cluster_modules_have_no_unguarded_requests_get():
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "extraction"
    modules = [
        "openserp_maps.py",
        "search_external_maps.py",
        "grok_search_maps.py",
        "validate_supplemental_urls.py",
        "supplemental_advanced.py",
        "fetch_endnotes.py",
    ]
    offenders = []
    for m in modules:
        src = (root / m).read_text()
        # any requests.get/post call is now a regression (all routed via safe_get /
        # fetch_text_safe / fetch_image_bytes)
        for mo in re.finditer(r"requests\.(get|post)\(", src):
            offenders.append(f"{m}: {mo.group(0)}")
    assert not offenders, "unguarded requests.get/post remain: " + "; ".join(offenders)
