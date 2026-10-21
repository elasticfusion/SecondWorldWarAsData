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
    with mock.patch("requests.Session.get") as g:
        assert safe_get("https://evil.example.org/x") is None
        g.assert_not_called()


def test_safe_get_disables_redirects_and_caps_body(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 200
    resp.iter_content = lambda n: [b"x" * 1000, b"y" * 1000]
    with mock.patch("requests.Session.get", return_value=resp) as g:
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
    with mock.patch("requests.Session.get", return_value=resp):
        text, status = fetch_text_safe("https://ex.org/page")
        assert status == 200 and "hi" in text


def test_fetch_text_safe_non_200_returns_status_no_text(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 404
    resp.iter_content = lambda n: [b""]
    with mock.patch("requests.Session.get", return_value=resp):
        text, status = fetch_text_safe("https://ex.org/missing")
        assert text is None and status == 404


def test_safe_get_network_error_is_non_fatal(monkeypatch):
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    with mock.patch("requests.Session.get", side_effect=OSError("boom")):
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
    with mock.patch("requests.Session.get", return_value=real):
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


# --- DNS-rebinding pin (round-b CRITICAL): connect to the VETTED IP, not a re-resolved host ---
def test_safe_get_pins_connection_to_vetted_ip(monkeypatch):
    """resolved_host_is_safe vets IPs; the actual TCP connection MUST go to a vetted IP (so a
    short-TTL rebind to 127.0.0.1/169.254.169.254 between check and connect can't slip through).
    Assert the URL handed to the transport uses the vetted IP as host + sends the real Host header.
    """
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    captured = {}

    def _fake_session_get(self, url, **kw):
        captured["url"] = url
        captured["host_header"] = (kw.get("headers") or {}).get("Host")
        captured["allow_redirects"] = kw.get("allow_redirects")
        r = mock.Mock()
        r.status_code = 200
        r.iter_content = lambda n: [b"ok"]
        return r

    with mock.patch("requests.Session.get", _fake_session_get):
        out = safe_get("https://victim.example.org/p")
        assert out is not None
    # connection pinned to the vetted IP, Host preserved, redirects off
    assert "93.184.216.34" in captured["url"]
    assert "victim.example.org" not in captured["url"]
    assert captured["host_header"] == "victim.example.org"
    assert captured["allow_redirects"] is False


def test_pinned_https_sets_tls_hostname_to_original_host(monkeypatch):
    """TLS-pin seam: the mounted adapter must set server_hostname+assert_hostname = the ORIGINAL
    host (not the IP), so cert validation binds to the real name. Intercept init_poolmanager kwargs.
    """
    from src.utils import url_hygiene as uh

    captured = {}
    orig_init = __import__("requests").adapters.HTTPAdapter.init_poolmanager

    def _spy(self, *a, **kw):
        captured.update(kw)
        return orig_init(self, *a, **kw)

    monkeypatch.setattr("requests.adapters.HTTPAdapter.init_poolmanager", _spy)
    # stop before an actual socket: make the session.get raise after mount
    monkeypatch.setattr(
        "requests.Session.get",
        lambda self, *a, **k: (_ for _ in ()).throw(OSError("stop")),
    )
    try:
        uh._pinned_get(
            "https://victim.example.org/p",
            "victim.example.org",
            ["93.184.216.34"],
            timeout=5,
            headers={},
        )
    except OSError:
        pass
    assert captured.get("server_hostname") == "victim.example.org"
    assert captured.get("assert_hostname") == "victim.example.org"


def test_pinned_get_falls_back_to_second_vetted_ip(monkeypatch):
    """Robustness: if the first vetted IP is unreachable, try the next (all are vetted/public)."""
    from src.utils import url_hygiene as uh

    seen = []

    def _get(self, url, **k):
        seen.append(url)
        if len(seen) == 1:
            raise OSError("first IP unreachable")  # first vetted IP down
        return mock.Mock(status_code=200, iter_content=lambda n: [b"ok"])

    monkeypatch.setattr("requests.Session.get", _get)
    resp = uh._pinned_get(
        "http://h.example.org/p",
        "h.example.org",
        ["203.0.113.1", "198.51.100.2"],
        timeout=5,
        headers={},
    )
    assert resp is not None and resp.status_code == 200
    assert len(seen) == 2  # fell back to the second vetted IP
    assert "198.51.100.2" in seen[1]


# --- §1.0c: clean_url rejects non-canonical numeric loopback/metadata encodings ---
def test_clean_url_rejects_hex_and_mixed_radix_loopback():
    from src.utils.url_hygiene import clean_url, _host_is_safe

    for h in (
        "0x7f.0x0.0x0.0x1",  # dotted-hex 127.0.0.1
        "127.0.0.0x1",  # mixed-radix 127.0.0.1
        "0x7f000001",  # bare hex 127.0.0.1
        "2130706433",  # decimal 127.0.0.1
        "0177.0.0.1",  # octal 127.0.0.1
        "127.1",  # short-form
        "0xA9FEA9FE",  # hex 169.254.169.254 (metadata)
    ):
        assert _host_is_safe(h) is False, h
        assert clean_url(f"http://{h}/x") is None, h
    # legitimate DNS names + public IPs still pass
    for h in ("en.wikipedia.org", "8.8.8.8", "a1.b2.c3.net", "3com.com"):
        assert _host_is_safe(h) is True, h


def test_safe_get_caps_compressed_body_gzip_bomb(monkeypatch):
    """A small highly-compressing body (huge resp.raw.tell()) must bail on the COMPRESSED cap, not
    only after decompressing to max_bytes."""
    monkeypatch.setattr(
        "src.utils.url_hygiene.resolved_host_is_safe", lambda host: ["93.184.216.34"]
    )
    resp = mock.Mock()
    resp.status_code = 200
    # small decompressed chunks, but resp.raw.tell() reports a huge compressed read
    resp.iter_content = lambda n: [b"x" * 100, b"y" * 100]
    resp.raw = mock.Mock()
    resp.raw.tell = lambda: 99_999_999  # far over the cap
    with mock.patch("requests.Session.get", return_value=resp):
        out = safe_get("https://ex.org/p", max_bytes=1500)
        assert out is not None
        assert len(out._content) <= 200  # bailed early on the compressed cap
