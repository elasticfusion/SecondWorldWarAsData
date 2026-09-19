"""Tests for situational-place geocoding (Grokipedia-first context; no network)."""

from src.enrichment.places_grok_geocode import GeocodeResult
from src.enrichment.situational_geocode import (
    encyclopedia_context,
    make_situational_geocoder,
)


class _Resp:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def json(self):
        import json as _j

        return _j.loads(self.text) if self.text else {}


class _FakeSession:
    """Fake session scripting Grokipedia search+page and Wikipedia responses."""

    def __init__(self, grok_search=None, grok_page=None, wiki=None):
        self._grok_search = grok_search
        self._grok_page = grok_page
        self._wiki = wiki
        self.urls = []

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=False):
        self.urls.append(url)
        if "grokipedia.com/search" in url:
            return _Resp(self._grok_search or "", 200 if self._grok_search else 404)
        if "grokipedia.com/page/" in url:
            return _Resp(self._grok_page or "", 200 if self._grok_page else 404)
        if "wikipedia.org" in url:
            return _Resp(self._wiki or "", 200 if self._wiki else 404)
        return _Resp("", 404)


def test_grokipedia_strictly_preferred_over_wikipedia(monkeypatch) -> None:
    # Grokipedia has no page for the full term but DOES for the anchor; Wikipedia
    # has the full term. Grokipedia (anchor) must win — Wikipedia is only a
    # fallback when Grokipedia yields nothing for any candidate.
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *_a, **_k: None)

    class _PickySession:
        def get(
            self, url, params=None, headers=None, timeout=None, allow_redirects=False
        ):
            q = (params or {}).get("q", "")
            if "grokipedia.com/search" in url:
                # Only the anchor "Aachen" has a Grokipedia page, not "Aachen Gap".
                if "Aachen Gap" in q:
                    return _Resp("", 200)  # no /page/, no data-slug
                return _Resp('data-slug="aachen" /page/aachen', 200)
            if "grokipedia.com/page/" in url:
                return _Resp("<p>Aachen, a German city near the border.</p>", 200)
            if "wikipedia.org" in url:
                return _Resp(
                    '{"query":{"pages":{"1":{"extract":"wiki full term"}}}}', 200
                )
            return _Resp("", 404)

    extract, source = encyclopedia_context("Aachen Gap", _PickySession())
    assert source == "grokipedia"
    assert "Aachen" in extract


def test_encyclopedia_prefers_grokipedia(tmp_path, monkeypatch) -> None:
    # Isolate the search cache to a temp dir so tests don't hit real cache.
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *_a, **_k: None)

    session = _FakeSession(
        grok_search='<a data-slug="aachen-gap">Aachen Gap</a> /page/aachen-gap',
        grok_page="<html><body>The Aachen Gap is the Stolberg Corridor "
        "between the Huertgen Forest and the Dutch border.</body></html>",
        wiki='{"query":{"pages":{"1":{"extract":"wiki text"}}}}',
    )
    extract, source = encyclopedia_context("Aachen Gap", session)
    assert source == "grokipedia"
    assert "Stolberg" in extract


def test_encyclopedia_falls_back_to_wikipedia(tmp_path, monkeypatch) -> None:
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *_a, **_k: None)

    session = _FakeSession(
        grok_search=None,  # Grokipedia miss
        wiki='{"query":{"pages":{"1":{"extract":"A corridor near Aachen."}}}}',
    )
    extract, source = encyclopedia_context("Aachen Gap", session)
    assert source == "wikipedia"
    assert "corridor" in extract.lower()


def test_encyclopedia_none_when_both_miss(monkeypatch) -> None:
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *_a, **_k: None)

    session = _FakeSession()  # everything 404
    extract, source = encyclopedia_context("Nowhere Gap", session)
    assert extract is None
    assert source == "none"


class _FakeGrok:
    def __init__(self, reply: GeocodeResult):
        self._reply = reply

    def chat_completion(
        self, prompt, system_prompt=None, temperature=0.1, cache_type=None
    ):
        self.prompt = prompt
        return self._reply.model_dump_json()


def test_situational_geocode_caps_confidence_and_flags(monkeypatch) -> None:
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *_a, **_k: None)

    session = _FakeSession(
        grok_search='data-slug="aachen-gap" /page/aachen-gap',
        grok_page="<p>Stolberg Corridor near Aachen.</p>",
    )
    grok = _FakeGrok(
        GeocodeResult(
            found=True, latitude=50.77, longitude=6.2, country="Germany", confidence=0.9
        )
    )
    geocode = make_situational_geocoder(session=session)
    place = {
        "current_name": "Aachen Gap",
        "event_mentions": [{"Sub_event_Name": "advance toward Aachen"}],
    }
    r = geocode("Aachen Gap", place, grok)
    assert r.found is True
    assert r.confidence <= 0.5  # approximate area -> capped
    assert "situational center" in r.note
    assert "grokipedia" in r.note  # context provenance recorded
    assert r.source == "situational"
    # The Grokipedia context made it into the prompt.
    assert "Stolberg" in grok.prompt


def test_situational_geocode_not_found_passthrough(monkeypatch) -> None:
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *_a, **_k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *_a, **_k: None)

    session = _FakeSession()
    grok = _FakeGrok(GeocodeResult(found=False, confidence=0.0))
    geocode = make_situational_geocoder(session=session)
    r = geocode("Unknowable Sector", {"current_name": "Unknowable Sector"}, grok)
    assert r.found is False
    assert r.source == "situational"
