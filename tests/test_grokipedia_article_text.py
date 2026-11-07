"""Grokipedia must contribute the ARTICLE text, not the search page.

XChannel-8 batch E2E (2026-11-07): 35 of 40 people got no biography. search_grokipedia returned
(and cached) the SEARCH-results page HTML; extract_biographical_data sends text[:5000] to Grok,
and the first 5000 chars of that page are <head>, a theme script and CSS (the matched person's
snippet sat at char 43,273). So Grokipedia never yielded a bio - only the 5 people Wikipedia
reached before it rate-limited (429) were enriched.
"""

from types import SimpleNamespace

import pytest
import requests

from src.enrichment import grokipedia as G
from src.extraction import enrich_biographies as EB
from src.utils import search_cache as SC

URL = "https://grokipedia.com/page/Matthew_Ridgway"
LEAD = (
    "Matthew Bunker Ridgway (March 3, 1895 \u2013 July 26, 1993) was a United States Army "
    "general who led the 82nd Airborne Division in Normandy."
)
ARTICLE = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'><script>var t=1;</script>"
    "<style>.x{}</style></head><body><nav>Contents Sign in</nav><main><article>"
    "<header><h1>Matthew Ridgway</h1></header><p>"
    + LEAD
    + " More text." * 40
    + "</p><script>track()</script></article></main></body></html>"
)
SEARCH = (
    "<!DOCTYPE html><html><head><script>" + "x" * 6000 + "</script></head><body>"
    '<a href="/page/Matthew_Ridgway" data-slug="Matthew_Ridgway" data-search-result-link="true"'
    ' data-scroll-anchor-text="Matthew B. Ridgway was a United States Army general">r</a>'
    "</body></html>"
)


def _resp(status, body, headers=None):
    raw = body.encode("utf-8")
    # .text decoded the way requests does without a charset header (ISO-8859-1): if the code
    # read .text instead of the bytes, the en dash would come out as mojibake.
    return SimpleNamespace(
        status_code=status,
        text=raw.decode("latin-1"),
        content=raw,
        encoding="ISO-8859-1",
        headers=headers or {},
    )


@pytest.fixture
def cache(monkeypatch):
    store = {}

    def put(source, query, result):
        if result is SC.TRANSIENT:
            return
        store[(source, query)] = result if result else "NOT_FOUND"

    monkeypatch.setattr(SC, "get_cached", lambda s, q: store.get((s, q)))
    monkeypatch.setattr(SC, "cache_result", put)
    EB._grokipedia_slugs.clear()
    return store


@pytest.fixture
def web(monkeypatch):
    pages = {}
    calls = []
    rendered = []

    def fetch(_provider, url, **_k):
        calls.append(url)
        for key, val in pages.items():
            if key in url:
                if isinstance(val, BaseException):
                    raise val
                return _resp(*val)
        return _resp(404, "")

    import src.utils.fetch_provenance as fp

    monkeypatch.setattr(fp, "recorded_fetch", fetch)
    monkeypatch.setattr(
        G, "fetch_grokipedia_rendered", lambda url: rendered.append(url) or None
    )
    return pages, calls, rendered


# --- fetch_grokipedia_article_text ---------------------------------------------------------


def test_article_text_is_the_article_not_the_page_chrome(web):
    pages, _, rendered = web
    pages["/page/Matthew_Ridgway"] = (200, ARTICLE)
    text = G.fetch_grokipedia_article_text(URL)
    assert text.startswith(
        "Matthew Ridgway " + LEAD
    )  # title kept from the article header
    assert "\u2013" in text  # bytes decoded correctly, no mojibake
    assert "var t" not in text and "Sign in" not in text and "track()" not in text
    assert rendered == []


def test_main_only_page_is_read_and_short_article_does_not_render(web):
    pages, _, rendered = web
    pages["/page/Stub"] = (
        200,
        "<html><body><main><p>Short stub.</p></main></body></html>",
    )
    assert (
        G.fetch_grokipedia_article_text("https://grokipedia.com/page/Stub")
        == "Short stub."
    )
    assert rendered == []  # a short article is still an article: no headless render


def test_shell_without_article_region_falls_back_to_render(web, monkeypatch):
    pages, _, _ = web
    pages["/page/Shell"] = (200, "<html><body><div id='app'></div></body></html>")
    monkeypatch.setattr(G, "fetch_grokipedia_rendered", lambda url: "Rendered body.")
    assert (
        G.fetch_grokipedia_article_text("https://grokipedia.com/page/Shell")
        == "Rendered body."
    )
    monkeypatch.setattr(G, "fetch_grokipedia_rendered", lambda url: None)
    # no browser / failed / recently rendered: unknown, never a 7-day negative
    assert (
        G.fetch_grokipedia_article_text("https://grokipedia.com/page/Shell")
        is SC.TRANSIENT
    )


def test_declared_charset_header_wins(web, monkeypatch):
    pages, _, _ = web
    body = "<html><body><article><p>Caf\u00e9 du Pont</p></article></body></html>"
    pages["/page/Cafe"] = (200, body, {"content-type": "text/html; charset=utf-8"})
    # the fake declares utf-8 in the header but its .encoding says latin-1: honour the header
    import src.utils.fetch_provenance as fp

    real = fp.recorded_fetch

    def fetch(provider, url, **k):
        r = real(provider, url, **k)
        r.encoding = "utf-8"
        return r

    monkeypatch.setattr(fp, "recorded_fetch", fetch)
    assert (
        G.fetch_grokipedia_article_text("https://grokipedia.com/page/Cafe")
        == "Caf\u00e9 du Pont"
    )


def test_non_200_and_network_errors_are_transient(web):
    pages, _, _ = web
    pages["/page/X"] = (503, "")
    pages["/page/Y"] = requests.ConnectionError("reset")
    assert (
        G.fetch_grokipedia_article_text("https://grokipedia.com/page/X") is SC.TRANSIENT
    )
    assert (
        G.fetch_grokipedia_article_text("https://grokipedia.com/page/Y") is SC.TRANSIENT
    )


def test_article_text_is_bounded(web):
    pages, _, _ = web
    pages["/page/Long"] = (200, "<article><p>" + "word " * 10000 + "</p></article>")
    text = G.fetch_grokipedia_article_text("https://grokipedia.com/page/Long")
    assert len(text) == G._MAX_RENDERED_CHARS


# --- people: search_grokipedia --------------------------------------------------------------


def test_person_search_returns_and_caches_article_text_with_source(cache, web):
    pages, _, _ = web
    pages["/search"] = (200, SEARCH)
    pages["/page/Matthew_Ridgway"] = (200, ARTICLE)
    text = EB.search_grokipedia("Matthew B. Ridgway")
    assert text.startswith("Matthew Ridgway " + LEAD)
    assert "<" not in text[:5000]  # what Grok sees is article text, not HTML
    assert cache[("grokipedia", "Matthew B. Ridgway")] == f"SOURCE_URL: {URL}\n{text}"
    assert (
        EB._grokipedia_slugs["Matthew B. Ridgway"] == "Matthew_Ridgway"
    )  # Wikipedia hint


def test_cached_article_restores_slug_and_strips_header(cache, web):
    _, calls, _ = web
    cache[("grokipedia", "Matthew B. Ridgway")] = f"SOURCE_URL: {URL}\n{LEAD}"
    assert EB.search_grokipedia("Matthew B. Ridgway") == LEAD
    assert EB._grokipedia_slugs["Matthew B. Ridgway"] == "Matthew_Ridgway"
    assert calls == []  # served from cache


def test_legacy_cached_search_html_is_upgraded_to_article_text(cache, web):
    pages, calls, _ = web
    pages["/page/Matthew_Ridgway"] = (200, ARTICLE)
    cache[("grokipedia", "Matthew B. Ridgway")] = SEARCH  # written by the old code
    text = EB.search_grokipedia("Matthew B. Ridgway")
    assert text.startswith("Matthew Ridgway " + LEAD)
    assert not any(
        "/search" in u for u in calls
    )  # slug re-derived from the cached page
    assert cache[("grokipedia", "Matthew B. Ridgway")].endswith(text)


def test_legacy_search_html_without_match_falls_through_to_live_search(cache, web):
    pages, calls, _ = web
    cache[("grokipedia", "Norman D. Cota")] = SEARCH  # no Cota in it
    pages["/search"] = (200, "<html><body>no results</body></html>")
    assert EB.search_grokipedia("Norman D. Cota") is None
    assert any("/search" in u for u in calls)
    assert cache[("grokipedia", "Norman D. Cota")] == "NOT_FOUND"  # genuine miss


def test_transient_article_failure_is_not_cached(cache, web):
    pages, _, _ = web
    pages["/search"] = (200, SEARCH)
    pages["/page/Matthew_Ridgway"] = (503, "")
    assert EB.search_grokipedia("Matthew B. Ridgway") is None
    assert ("grokipedia", "Matthew B. Ridgway") not in cache  # retried next run


def test_page_with_empty_article_is_a_negative(cache, web):
    pages, _, _ = web
    pages["/search"] = (200, SEARCH)
    pages["/page/Matthew_Ridgway"] = (
        200,
        "<html><body><article> </article></body></html>",
    )
    assert EB.search_grokipedia("Matthew B. Ridgway") is None
    assert (
        cache[("grokipedia", "Matthew B. Ridgway")] == "NOT_FOUND"
    )  # no repeat fetches


def test_shell_page_without_render_is_not_negative_cached(cache, web):
    pages, _, _ = web
    pages["/search"] = (200, SEARCH)
    pages["/page/Matthew_Ridgway"] = (200, "<html><body><div></div></body></html>")
    assert EB.search_grokipedia("Matthew B. Ridgway") is None
    assert ("grokipedia", "Matthew B. Ridgway") not in cache


def test_search_429_is_not_negative_cached(cache, web):
    pages, _, _ = web
    pages["/search"] = (429, "")
    assert EB.search_grokipedia("Matthew B. Ridgway") is None
    assert ("grokipedia", "Matthew B. Ridgway") not in cache


def test_path_like_slug_cannot_change_the_url_path_or_query(cache, web):
    pages, calls, _ = web
    pages["/search"] = (
        200,
        SEARCH.replace(
            'data-slug="Matthew_Ridgway"', 'data-slug="Matthew_Ridgway/../../x?y=1"'
        ),
    )
    EB.search_grokipedia("Matthew B. Ridgway")
    page_calls = [u for u in calls if "/page/" in u]
    assert page_calls == [
        "https://grokipedia.com/page/Matthew_Ridgway%2F..%2F..%2Fx%3Fy%3D1"
    ]


@pytest.mark.parametrize(
    "slug,url",
    [
        ("James_M._Gavin", "https://grokipedia.com/page/James_M._Gavin"),  # real: dots
        ("Gavin_(general)", "https://grokipedia.com/page/Gavin_(general)"),
        (
            "Sainte-M\u00e8re-\u00c9glise",
            "https://grokipedia.com/page/Sainte-M%C3%A8re-%C3%89glise",
        ),
        ("..", None),
        ("", None),
        ("a\nb", None),
    ],
)
def test_grokipedia_page_url(slug, url):
    assert G.grokipedia_page_url(slug) == url


def test_rejected_slug_alerts_and_is_never_fetched(cache, web, caplog, monkeypatch):
    import logging

    pages, calls, _ = web
    pages["/search"] = (200, SEARCH)
    monkeypatch.setattr(EB, "_match_grokipedia_page", lambda *a: "..")
    with caplog.at_level(logging.WARNING):
        assert EB.search_grokipedia("Matthew B. Ridgway") is None
    assert not any("/page/" in u for u in calls)
    assert any("unclean_slug" in r.getMessage() for r in caplog.records)


def test_accented_place_link_is_encoded_once(cache, web):
    from src.extraction import enrich_places as EP

    pages, _, _ = web
    pages["/search"] = (200, '<a href="/page/Sainte-M%C3%A8re-%C3%89glise">x</a>')
    assert (
        EP._search_grokipedia_place("Sainte-M\u00e8re-\u00c9glise")
        == "https://grokipedia.com/page/Sainte-M%C3%A8re-%C3%89glise"
    )


def test_article_chrome_buttons_and_menus_are_dropped(web):
    pages, _, _ = web
    pages["/page/Matthew_Ridgway"] = (
        200,
        "<article><header><h1>Matthew Ridgway</h1><div class='menu flex'><button>"
        "<span>Ara</span></button><button>1x</button></div></header><p>"
        + LEAD
        + "</p></article>",
    )
    assert G.fetch_grokipedia_article_text(URL) == "Matthew Ridgway " + LEAD


def test_biography_source_records_the_grokipedia_page(cache, web, monkeypatch):
    pages, _, _ = web
    pages["/search"] = (200, SEARCH)
    pages["/page/Matthew_Ridgway"] = (200, ARTICLE)
    monkeypatch.setattr(
        EB,
        "extract_biographical_data",
        lambda name, text, source, grok: {
            "birth_date": "1895-03-03",
            "_source": source,
        },
    )
    profile = {}
    assert EB._search_and_enrich(
        "Matthew B. Ridgway", "Grokipedia", EB.search_grokipedia, profile, None
    )
    (src,) = profile["biography_sources"]
    assert (src["source"], src["page"]) == ("Grokipedia", URL)  # citable provenance


# --- places --------------------------------------------------------------------------------


def test_place_search_network_error_and_non_200_are_not_negative_cached(cache, web):
    from src.extraction import enrich_places as EP

    pages, _, _ = web
    pages["/search"] = requests.ConnectionError("reset")
    assert EP._search_grokipedia_place("Carentan") is None
    pages["/search"] = (429, "")
    assert EP._search_grokipedia_place("Carentan") is None
    assert ("grokipedia_place", "Carentan") not in cache


def test_place_search_genuine_miss_and_hit(cache, web):
    from src.extraction import enrich_places as EP

    pages, _, _ = web
    pages["/search"] = (200, '<a href="/page/Carentan">Carentan</a>')
    assert (
        EP._search_grokipedia_place("Carentan")
        == "https://grokipedia.com/page/Carentan"
    )
    pages["/search"] = (200, "<html>no /page/ links</html>")
    assert EP._search_grokipedia_place("Nowhere") is None
    assert cache[("grokipedia_place", "Nowhere")] == "NOT_FOUND"


# --- provenance hardening (follow-up review) ----------------------------------------------


def test_llm_cannot_inject_a_source_url():
    # Grok's JSON is derived from untrusted page text; a "_source_url" in it is dropped.
    class _Grok:
        def extract_json(self, **_k):
            return {"birth_date": "1895", "_source_url": "https://evil.example/x"}

    data = EB.extract_biographical_data("X", "text", "Wikipedia", _Grok())
    assert "_source_url" not in data and data["_source"] == "Wikipedia"


@pytest.mark.parametrize(
    "page,kept",
    [
        ("https://grokipedia.com/page/Matthew_Ridgway", True),
        ("https://evil.example/page/x", False),
        (None, False),
    ],
)
def test_merge_accepts_only_a_grokipedia_page(page, kept):
    profile = {}
    data = {"birth_date": "1895-03-03", "_source": "Grokipedia"}
    if page:
        data["_source_url"] = page
    assert EB._merge_enrichment(profile, data)
    (src,) = profile["biography_sources"]
    assert src["page"] == (page if kept else None)
    assert "_source_url" not in profile and "_source" not in profile


def test_wikipedia_source_has_no_page(cache, web, monkeypatch):
    monkeypatch.setattr(
        EB,
        "extract_biographical_data",
        lambda name, text, source, grok: {"birth_date": "1895", "_source": source},
    )
    profile = {}
    EB._grokipedia_slugs["X"] = (
        "X"  # a Grokipedia slug must not leak onto a Wikipedia source
    )
    assert EB._search_and_enrich("X", "Wikipedia", lambda n: "bio text", profile, None)
    assert profile["biography_sources"][0]["page"] is None


def test_reference_path_records_its_grokipedia_page(cache, web, monkeypatch):
    pages, _, _ = web
    pages["/search"] = (200, SEARCH)
    pages["/page/Matthew_Ridgway"] = (200, ARTICLE)
    monkeypatch.setattr(
        EB,
        "extract_biographical_data",
        lambda name, text, source, grok: {"birth_date": "1895", "_source": source},
    )
    (data,) = EB.search_references(["Matthew B. Ridgway"], None)
    assert data["_source_url"] == URL


def test_accented_slug_round_trips_through_the_cache(cache, web):
    _, calls, _ = web
    url = "https://grokipedia.com/page/Karl_D%C3%B6nitz"
    cache[("grokipedia", "Karl D\u00f6nitz")] = f"SOURCE_URL: {url}\nBio."
    assert EB.search_grokipedia("Karl D\u00f6nitz") == "Bio."
    assert EB._grokipedia_slugs["Karl D\u00f6nitz"] == "Karl_D\u00f6nitz"
    assert EB._source_page_url("Karl D\u00f6nitz", "Grokipedia") == url  # encoded once
    assert calls == []


def test_oversized_page_parse_is_bounded(web, monkeypatch):
    pages, _, _ = web
    monkeypatch.setattr(G, "_MAX_PAGE_BYTES", 200)
    pages["/page/Big"] = (
        200,
        "<article><p>" + "a" * 100 + "</p></article>" + "x" * 5000,
    )
    seen = {}
    real = G._article_text_from_html
    monkeypatch.setattr(
        G,
        "_article_text_from_html",
        lambda body: seen.setdefault("n", len(body)) and real(body),
    )
    G.fetch_grokipedia_article_text("https://grokipedia.com/page/Big")
    assert seen["n"] == 200


# --- accented names: the search page is UTF-8 without a charset header --------------------

DONITZ_SEARCH = (
    '<a href="/page/Karl_D%C3%B6nitz" data-slug="Karl_D\u00f6nitz" data-search-result-link="true"'
    ' data-scroll-anchor-text="Karl D\u00f6nitz was a German admiral">k</a>'
)
DONITZ_ARTICLE = (
    "<article><p>Karl D\u00f6nitz (1891\u20131980) was a German admiral.</p></article>"
)


def test_accented_person_is_matched_and_fetched(cache, web):
    # resp.text (ISO-8859-1) reads "Karl_DÃ¶nitz": the old code never matched any accented name
    pages, calls, _ = web
    pages["/search"] = (200, DONITZ_SEARCH)
    pages["/page/Karl_D%C3%B6nitz"] = (200, DONITZ_ARTICLE)
    assert EB.search_grokipedia("Karl D\u00f6nitz") == (
        "Karl D\u00f6nitz (1891\u20131980) was a German admiral."
    )
    assert "https://grokipedia.com/page/Karl_D%C3%B6nitz" in calls


def test_accented_place_and_resolver_match(cache, web):
    from src.extraction import enrich_places as EP

    pages, _, _ = web
    pages["/search"] = (200, '<a href="/page/Sainte-M\u00e8re-\u00c9glise">x</a>')
    assert (
        EP._search_grokipedia_place("Sainte-M\u00e8re-\u00c9glise")
        == "https://grokipedia.com/page/Sainte-M%C3%A8re-%C3%89glise"
    )
    assert G._scrape_grokipedia_url("Karl D\u00f6nitz", 10) is not None


def test_response_text_honours_a_declared_charset():
    body = "Caf\u00e9".encode("cp1252")
    resp = SimpleNamespace(
        content=body,
        encoding="cp1252",
        headers={"content-type": "text/html; charset=windows-1252"},
    )
    assert G.response_text(resp) == "Caf\u00e9"
    undeclared = SimpleNamespace(
        content="Caf\u00e9".encode(), encoding="ISO-8859-1", headers={}
    )
    assert G.response_text(undeclared) == "Caf\u00e9"  # UTF-8, not requests' guess


# --- final review: shared decode, slug unescape, shell memo, situational, source_section ----


def test_response_text_survives_an_unknown_declared_charset():
    from src.utils.http_text import response_text

    resp = SimpleNamespace(
        content="Caf\u00e9".encode(),
        encoding="x-no-such-codec",
        headers={"content-type": "text/html; charset=x-no-such-codec"},
    )
    assert response_text(resp) == "Caf\u00e9"  # LookupError -> UTF-8, never raises
    assert response_text(SimpleNamespace()) == ""


def test_escaped_data_slug_is_unescaped_once():
    html = '<a data-slug="A_&amp;_B" data-search-result-link="true">x</a>'
    assert G.first_page_url(html) == "https://grokipedia.com/page/A_%26_B"


def test_shell_page_is_rendered_at_most_once_per_process(web, monkeypatch):
    pages, _, _ = web
    pages["/page/Shell2"] = (200, "<html><body><div id='app'></div></body></html>")
    renders = []
    monkeypatch.setattr(G, "fetch_grokipedia_rendered", lambda url: renders.append(url))
    monkeypatch.setattr(G, "_SHELL_UNRENDERABLE", set())
    for _ in range(3):
        assert (
            G.fetch_grokipedia_article_text("https://grokipedia.com/page/Shell2")
            is SC.TRANSIENT
        )
    assert len(renders) == 1


def test_missing_playwright_warns_once_with_the_action(monkeypatch, caplog):
    import builtins
    import logging

    from src.utils.observe import reset_counted_warnings

    reset_counted_warnings()
    real_import = builtins.__import__

    def _no_playwright(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_playwright)
    with caplog.at_level(logging.WARNING):
        G._render_grokipedia_text("https://grokipedia.com/page/A", 1000)
        G._render_grokipedia_text("https://grokipedia.com/page/B", 1000)
    msgs = [
        r.getMessage() for r in caplog.records if "playwright_missing" in r.getMessage()
    ]
    assert len(msgs) == 1 and "playwright_install_chromium" in msgs[0]


def test_situational_context_is_article_text_in_its_own_namespace(cache, web):
    import src.enrichment.situational_geocode as SG

    pages, _, _ = web
    pages["/search"] = (200, DONITZ_SEARCH)
    pages["/page/Karl_D%C3%B6nitz"] = (200, DONITZ_ARTICLE)
    text = SG.grokipedia_context("D\u00f6nitz", session=None)
    assert text == "Karl D\u00f6nitz (1891\u20131980) was a German admiral."
    assert cache[("grokipedia_situational", "D\u00f6nitz")] == text
    assert (
        "grokipedia_place",
        "D\u00f6nitz",
    ) not in cache  # no clash with enrich_places URLs


def test_situational_transient_page_is_not_cached(cache, web):
    import src.enrichment.situational_geocode as SG

    pages, _, _ = web
    pages["/search"] = (200, DONITZ_SEARCH)
    pages["/page/Karl_D%C3%B6nitz"] = (503, "")
    assert SG.grokipedia_context("D\u00f6nitz", session=None) is None
    assert ("grokipedia_situational", "D\u00f6nitz") not in cache


def test_legacy_situational_text_under_place_key_is_not_served_as_a_url(cache, web):
    from src.extraction import enrich_places as EP

    pages, calls, _ = web
    cache[("grokipedia_place", "Carentan")] = (
        "Carentan Fact-checked by Grok The town..."
    )
    pages["/search"] = (200, '<a href="/page/Carentan">Carentan</a>')
    assert (
        EP._search_grokipedia_place("Carentan")
        == "https://grokipedia.com/page/Carentan"
    )
    assert any("/search" in u for u in calls)  # looked up live, entry overwritten
    assert (
        cache[("grokipedia_place", "Carentan")]
        == "https://grokipedia.com/page/Carentan"
    )


def test_apostrophe_slug_is_kept_and_template_fragment_skipped():
    html = (
        "<a data-slug=\"' + escHtml(s.slug) + '\"></a>"
        '<a data-slug="John_W._O\'Daniel" data-search-result-link="true">x</a>'
    )
    assert G.first_page_url(html) == "https://grokipedia.com/page/John_W._O%27Daniel"


def test_situational_article_parser_error_degrades_to_transient(
    cache, web, monkeypatch
):
    import src.enrichment.situational_geocode as SG

    pages, _, _ = web
    pages["/search"] = (200, DONITZ_SEARCH)

    def boom(*_a, **_k):
        raise RecursionError("pathological html")

    monkeypatch.setattr(G, "fetch_grokipedia_article_text", boom)
    assert SG.grokipedia_context("D\u00f6nitz", session=None) is None
    assert ("grokipedia_situational", "D\u00f6nitz") not in cache
