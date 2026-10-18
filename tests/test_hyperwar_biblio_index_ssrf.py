"""HIGH#6: SSRF guard for the build-time HyperWar bibliography index generator.

The generator scrapes hrefs out of fetched (untrusted) HTML and would otherwise follow/store ANY
absolute URL. These tests lock in the HTTPS + host-allowlist guard and its observability (the build
summary must distinguish "no bibliography link" from "guard blocked a non-allowlisted link").
"""

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_hyperwar_biblio_index",
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_hyperwar_biblio_index.py",
)
bh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bh)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/USA-E-Siegfried-Bib.html",
        "https://ibiblio.org/hyperwar/x.html",
    ],
)
def test_guard_accepts_allowlisted_https(url):
    assert bh._is_safe_biblio_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://www.ibiblio.org/x",  # not https
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata SSRF
        "http://localhost:8080/",  # localhost SSRF
        "https://10.0.0.5/internal",  # private range
        "https://evil.com/x",  # external host
        "https://www.ibiblio.org.evil.com/x",  # suffix-spoof
        "https://user:pass@www.ibiblio.org/x",  # embedded credentials
        "file:///etc/passwd",  # alternate scheme
        "ftp://ibiblio.org/x",
        "",
        "not a url",
    ],
)
def test_guard_rejects_unsafe(url):
    assert bh._is_safe_biblio_url(url) is False


def test_get_refuses_non_allowlisted(capsys, monkeypatch):
    # _get must refuse before any network call; urlopen should never be invoked.
    called = {"n": 0}
    monkeypatch.setattr(
        bh.urllib.request, "urlopen", lambda *a, **k: called.__setitem__("n", 1)
    )
    assert bh._get("https://evil.com/x") is None
    assert called["n"] == 0


def test_discover_skips_offhost_href_and_counts(monkeypatch):
    # index HTML offers ONLY an off-host "Bibliography" link -> rejected -> None, counter bumped.
    html = '<a href="https://evil.com/Bibliography.html">Bibliography</a>'
    monkeypatch.setattr(bh, "_get", lambda url, timeout=30: html)
    bh._guard_rejections = 0
    assert bh.discover_biblio_url("USA-E-Siegfried") is None
    assert bh._guard_rejections == 1


def test_discover_accepts_relative_href(monkeypatch):
    # A relative "Bibliography" href is pinned under BASE (safe) and returned.
    html = '<a href="USA-E-Siegfried-Bib.html">Bibliography</a>'
    monkeypatch.setattr(bh, "_get", lambda url, timeout=30: html)
    out = bh.discover_biblio_url("USA-E-Siegfried")
    assert out == f"{bh.BASE}/USA-E-Siegfried/USA-E-Siegfried-Bib.html"


def test_build_distinguishes_guard_block_from_no_link(monkeypatch, capsys):
    # Volume whose only href is off-host -> build reports it as REJECTED by the guard, not "no link".
    html = '<a href="https://evil.com/Bibliography.html">Bibliography</a>'
    monkeypatch.setattr(bh, "_get", lambda url, timeout=30: html)
    bh._guard_rejections = 0
    index = bh.build(["USA-E-Siegfried"])
    assert index["USA-E-Siegfried"]["biblio_url"] is None
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "REJECTED by SSRF guard" in combined
    assert "SSRF guard blocked a bibliography href for 1 volume" in combined


@pytest.mark.parametrize(
    "url",
    [
        "https://www.ibiblio.org\\@evil.com/x",  # backslash host confusion
        "https:evil.com",  # no // — classic bypass
        "https://www.ibiblio.org:8080@evil.com/x",
    ],
)
def test_guard_rejects_confusion_bypasses(url):
    assert bh._is_safe_biblio_url(url) is False


def test_redirect_to_offhost_is_blocked():
    """WARNING fix: the redirect handler re-validates the target; an off-host 302 is refused."""
    import urllib.error

    h = bh._AllowlistRedirectHandler()
    with pytest.raises(urllib.error.HTTPError):
        h.redirect_request(
            req=None,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="http://169.254.169.254/latest/meta-data/",
        )


def test_get_fails_safe_when_open_raises(monkeypatch):
    monkeypatch.setattr(
        bh._OPENER,
        "open",
        lambda *a, **k: (_ for _ in ()).throw(Exception("boom")),
    )
    assert bh._get("https://www.ibiblio.org/x") is None
