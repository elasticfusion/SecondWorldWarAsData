"""Tests for source_section article fetch (Grokipedia + Wikipedia textual enrichment)."""

from unittest.mock import MagicMock, patch

from src.extraction import source_section_articles as ssa


def _resp(status=200, json_data=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = json_data or {}
    return r


def test_wikipedia_article_parses_text_and_references():
    parse_json = {
        "parse": {
            "title": "Battle of the Bulge",
            "text": {"*": "<p>World War II battle fought in the Ardennes.</p>"},
            "externallinks": ["http://a.example", "http://b.example"],
        }
    }
    with patch.object(ssa.requests, "get", return_value=_resp(json_data=parse_json)):
        art = ssa.fetch_wikipedia_article("Battle of the Bulge")
    assert art["source"] == "wikipedia"
    assert "Ardennes" in art["extract"]
    assert art["url"].endswith("/wiki/Battle_of_the_Bulge")
    assert len(art["references"]) == 2
    assert art["references"][0]["source"] == "wikipedia-reference"
    assert art["license"] == ssa._WIKI_LICENSE
    assert art["retrieved_at"]


def test_wikipedia_missing_page_returns_none():
    with patch.object(
        ssa.requests,
        "get",
        return_value=_resp(json_data={"error": {"code": "missingtitle"}}),
    ):
        assert ssa.fetch_wikipedia_article("Nonexistent Thing") is None


def test_is_clean_slug_rejects_js_fragments():
    assert ssa._is_clean_slug("Battle_of_the_Bulge")
    assert not ssa._is_clean_slug("' + escHtml(s.slug) + '")
    assert not ssa._is_clean_slug("has spaces")
    assert not ssa._is_clean_slug("")


def test_grokipedia_graceful_url_only_when_no_text():
    # search resolves a clean slug; page body yields no extractable text -> URL-only record
    search = _resp(text='<a href="/page/Battle_of_the_Bulge">x</a>')
    page = _resp(text="<html><body></body></html>")
    with patch.object(ssa.requests, "get", side_effect=[search, page]):
        art = ssa.fetch_grokipedia_article("Battle of the Bulge")
    assert art is not None
    assert art["source"] == "grokipedia"
    assert art["url"] == "https://grokipedia.com/page/Battle_of_the_Bulge"
    assert art["extract"] is None  # graceful: URL-only
    assert art["license"] is None  # null-over-fake


def test_grokipedia_no_resolution_returns_none():
    with patch.object(ssa.requests, "get", return_value=_resp(text="no results here")):
        assert ssa.fetch_grokipedia_article("Battle of the Bulge") is None


def test_fetch_reference_articles_combines_sources():
    with (
        patch.object(
            ssa, "fetch_grokipedia_article", return_value={"source": "grokipedia"}
        ),
        patch.object(
            ssa, "fetch_wikipedia_article", return_value={"source": "wikipedia"}
        ),
    ):
        arts = ssa.fetch_reference_articles(
            {"name": "Battle of the Bulge", "wikipedia_title": "Battle of the Bulge"}
        )
    assert [a["source"] for a in arts] == ["grokipedia", "wikipedia"]


def test_fetch_reference_articles_no_name_empty():
    assert ssa.fetch_reference_articles({"name": None}) == []
