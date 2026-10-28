"""Tier-1: places.sameAs folds the already-resolved wikipedia_url/grokipedia_url (deterministic)."""

from src.extraction.enrich_places import _add_place_sameas


def test_sameas_from_wikipedia_url():
    p = {"PlaceID": "x", "wikipedia_url": "https://en.wikipedia.org/wiki/Saint-L%C3%B4"}
    _add_place_sameas(p)
    assert p["sameAs"] == ["https://en.wikipedia.org/wiki/Saint-L%C3%B4"]


def test_sameas_adds_grokipedia_and_dedups():
    p = {
        "PlaceID": "x",
        "wikipedia_url": "https://en.wikipedia.org/wiki/X",
        "grokipedia_url": "https://grokipedia.com/X",
    }
    _add_place_sameas(p)
    assert p["sameAs"] == [
        "https://en.wikipedia.org/wiki/X",
        "https://grokipedia.com/X",
    ]
    _add_place_sameas(p)  # idempotent
    assert p["sameAs"] == [
        "https://en.wikipedia.org/wiki/X",
        "https://grokipedia.com/X",
    ]


def test_sameas_noop_when_no_urls():
    p = {"PlaceID": "x"}
    _add_place_sameas(p)
    assert "sameAs" not in p


def test_sameas_preserves_existing():
    p = {
        "PlaceID": "x",
        "sameAs": ["https://www.wikidata.org/wiki/Q1"],
        "wikipedia_url": "https://en.wikipedia.org/wiki/X",
    }
    _add_place_sameas(p)
    assert p["sameAs"] == [
        "https://www.wikidata.org/wiki/Q1",
        "https://en.wikipedia.org/wiki/X",
    ]


def test_sameas_failsafe_on_bad_input():
    p = {"PlaceID": "x", "wikipedia_url": 123}  # non-str -> skipped, no crash
    _add_place_sameas(p)
    assert "sameAs" not in p


# --- T2-A: Wikidata QID piggybacked on the existing Wikipedia call ---
def test_wikidata_qid_from_pageprops():
    from src.extraction.enrich_places import _build_wiki_place_result

    pd = {
        "title": "Saint-Lô",
        "original": {"source": ""},
        "pageprops": {"wikibase_item": "Q181943"},
    }
    r = _build_wiki_place_result("Saint-Lô", pd)
    assert r["wikidata_url"] == "https://www.wikidata.org/wiki/Q181943"


def test_wikidata_qid_absent_is_none():
    from src.extraction.enrich_places import _build_wiki_place_result

    r = _build_wiki_place_result(
        "Nowhere", {"title": "Nowhere", "original": {"source": ""}}
    )
    assert r["wikidata_url"] is None


def test_sameas_extra_folds_wikidata():
    p = {"PlaceID": "x", "wikipedia_url": "https://en.wikipedia.org/wiki/Saint-L%C3%B4"}
    _add_place_sameas(p, extra=["https://www.wikidata.org/wiki/Q181943"])
    assert p["sameAs"] == [
        "https://en.wikipedia.org/wiki/Saint-L%C3%B4",
        "https://www.wikidata.org/wiki/Q181943",
    ]
    _add_place_sameas(p, extra=["https://www.wikidata.org/wiki/Q181943"])  # idempotent
    assert p["sameAs"].count("https://www.wikidata.org/wiki/Q181943") == 1
