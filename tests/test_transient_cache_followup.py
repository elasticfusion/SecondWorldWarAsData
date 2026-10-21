"""Regression — §1.0c transient-cache follow-up (the 3 remaining sites)."""

from unittest import mock

from src.utils import search_cache as SC


def test_situational_grokipedia_transient_not_cached(monkeypatch):
    import src.enrichment.situational_geocode as SG

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    sess = mock.Mock()
    sess.get.return_value = mock.Mock(status_code=503, text="")
    assert SG.grokipedia_context("Aachen Gap", sess) is None
    assert calls == []  # transient -> no negative cache


def test_situational_wiki_transient_not_cached(monkeypatch):
    import src.enrichment.situational_geocode as SG

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    sess = mock.Mock()
    sess.get.return_value = mock.Mock(status_code=503)
    assert SG.wikipedia_context("Aachen Gap", sess) is None
    assert calls == []  # both title attempts transient -> no negative cache


def test_geocode_transient_sets_retryable_status():
    from src.enrichment.places_grok_geocode import (
        _apply_result,
        GeocodeResult,
        _already_attempted,
        _STATUS_ERROR,
        _STATUS_NOT_FOUND,
    )

    place = {}
    st = _apply_result(
        place,
        GeocodeResult(found=False, note="error: timeout", source="osm", transient=True),
    )
    assert st == _STATUS_ERROR
    assert not _already_attempted(place)  # RETRIED next run (not permanently skipped)
    # a genuine not-found is still terminal
    place2 = {}
    st2 = _apply_result(
        place2, GeocodeResult(found=False, note="no OSM match", source="osm")
    )
    assert st2 == _STATUS_NOT_FOUND and _already_attempted(place2)
