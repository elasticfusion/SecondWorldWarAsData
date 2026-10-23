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
    sess.request.return_value = mock.Mock(status_code=503, text="")
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
    sess.request.return_value = mock.Mock(status_code=503)
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


def test_run_openserp_queries_saw_200_flag(monkeypatch):
    import src.enrichment.bibliography_resolver as BR

    monkeypatch.setattr("time.sleep", lambda *a: None)
    sess = mock.Mock()
    # all queries 503 -> no url, saw_200 False (caller must NOT negative-cache)
    sess.get.return_value = mock.Mock(status_code=503)
    url, saw = BR._run_openserp_queries(
        sess, "http://osrp", ["q1", "q2"], "ref", None, None
    )
    assert url is None and saw is False
    # a 200 with no usable result -> saw_200 True (genuine miss, caller negative-caches)
    sess.get.return_value = mock.Mock(status_code=200, json=lambda: {"results": []})
    url2, saw2 = BR._run_openserp_queries(
        sess, "http://osrp", ["q1"], "ref", None, None
    )
    assert url2 is None and saw2 is True


def test_situational_wiki_mixed_transient_then_hit(monkeypatch):
    import src.enrichment.situational_geocode as SG

    cached_val = {}
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: cached_val.update({q: r})
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    # first title (bare term) transient 503; second title (WWII) real 200 hit
    calls = {"n": 0}

    def _request(method, url, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return mock.Mock(status_code=503)
        return mock.Mock(
            status_code=200,
            json=lambda: {"query": {"pages": {"1": {"extract": "A real place."}}}},
        )

    sess = mock.Mock()
    sess.request = _request
    out = SG.wikipedia_context("Aachen Gap", sess)
    assert (
        out == "A real place."
    )  # the second (hit) wins despite the first being transient
    assert cached_val.get("Aachen Gap") == "A real place."  # positive-cached
