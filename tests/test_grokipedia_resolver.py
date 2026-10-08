"""Tests for the shared generic Grokipedia resolver."""

from unittest.mock import MagicMock, patch

from src.enrichment import grokipedia as gk


def _resp(status=200, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def test_is_clean_slug():
    assert gk.is_clean_slug("Battle_of_the_Bulge")
    assert gk.is_clean_slug("M4-Sherman")
    assert not gk.is_clean_slug("' + escHtml(s.slug) + '")
    assert not gk.is_clean_slug("has spaces")
    assert not gk.is_clean_slug("")


def test_resolve_returns_clean_url(monkeypatch):
    monkeypatch.setattr(gk, "get_cached", lambda *a, **k: None, raising=False)
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
        patch.object(
            gk.requests,
            "get",
            return_value=_resp(text='<a href="/page/M4_Sherman">x</a>'),
        ),
    ):
        url = gk.resolve_grokipedia_url("M4 Sherman")
    assert url == "https://grokipedia.com/page/M4_Sherman"


def test_resolve_rejects_js_fragment_returns_none():
    with (
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
        patch.object(
            gk.requests,
            "get",
            return_value=_resp(text="/page/' + escHtml(s.slug) + '"),
        ),
    ):
        assert gk.resolve_grokipedia_url("Weird Thing") is None


def test_resolve_short_name_is_none():
    assert gk.resolve_grokipedia_url("ab") is None


def test_resolve_negative_cache():
    with patch("src.utils.search_cache.get_cached", return_value="NOT_FOUND"):
        assert gk.resolve_grokipedia_url("Battle of the Bulge") is None


def test_fetch_rendered_empty_url_returns_none():
    from src.enrichment.grokipedia import fetch_grokipedia_rendered

    assert fetch_grokipedia_rendered("") is None


def test_fetch_rendered_graceful_when_playwright_missing(monkeypatch):
    import builtins

    from src.enrichment import grokipedia as gk

    real_import = builtins.__import__

    def _no_playwright(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("playwright not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_playwright)
    # Browser/lib unavailable -> graceful None (URL-only), never raises.
    assert gk.fetch_grokipedia_rendered("https://grokipedia.com/page/X") is None
