"""Regression — §1.0c WARNING: transient fetch errors must NOT be negative-cached.

A timeout / 5xx / 429 / network exception is distinct from a genuine 200-empty miss. Negative-caching
a transient failure (NEGATIVE_TTL_DAYS=7) suppresses a genuinely-available resource for a week. The
TRANSIENT sentinel lets a fetcher signal 'do not cache'.
"""

from unittest import mock

from src.utils import search_cache as SC


def test_cache_result_noops_on_transient():
    backend = mock.Mock()
    with mock.patch.object(SC, "_get_backend", return_value=backend):
        SC.cache_result("src", "q", SC.TRANSIENT)
        backend.put.assert_not_called()  # transient -> NOT cached
        # a genuine miss IS negative-cached
        SC.cache_result("src", "q", None)
        assert backend.put.call_args.args[1] == "NOT_FOUND"


def test_grokipedia_transient_not_negative_cached(monkeypatch):
    import src.enrichment.grokipedia as G

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    # non-200 -> TRANSIENT -> resolve returns None, and cache_result NOT called with a negative
    resp = mock.Mock(status_code=503, text="")
    monkeypatch.setattr(
        "src.utils.http_pool.get_session",
        lambda: mock.Mock(request=lambda *a, **k: resp),
    )
    assert G.resolve_grokipedia_url("Some Unit Name") is None
    assert calls == []  # no cache write on transient


def test_grokipedia_genuine_miss_is_negative_cached(monkeypatch):
    import src.enrichment.grokipedia as G

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    # 200 with no /page/ -> genuine miss -> negative-cached (None)
    resp = mock.Mock(status_code=200, text="<html>no results</html>")
    monkeypatch.setattr(
        "src.utils.http_pool.get_session",
        lambda: mock.Mock(request=lambda *a, **k: resp),
    )
    assert G.resolve_grokipedia_url("Nonexistent Thing") is None
    assert calls and calls[0][1] is None  # negative-cached


def test_noaa_station_transient_not_negative_cached(monkeypatch):
    import src.enrichment.noaa_weather as N

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    # both station-box queries hit a transient (503) -> station None, but NO negative cache written
    monkeypatch.setattr(N, "_search_stations_box", lambda *a, **k: SC.TRANSIENT)
    assert N.find_nearest_station(50.0, 6.0, "1944-12-16", "tok") is None
    assert calls == []  # no negative-cache on transient


def test_noaa_station_genuine_miss_is_negative_cached(monkeypatch):
    import src.enrichment.noaa_weather as N

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    # a real empty 200 (no stations) -> negative-cache
    monkeypatch.setattr(N, "_search_stations_box", lambda *a, **k: {"results": []})
    assert N.find_nearest_station(50.0, 6.0, "1944-12-16", "tok") is None
    assert calls and calls[0][1] is None


def test_wikipedia_suffix_loop_transient_skips_negative_cache(monkeypatch):
    import src.enrichment.equipment_wikipedia as EW

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    # every lookup (primary + suffixes) is transient -> no result, NO negative cache
    monkeypatch.setattr(EW, "_lookup_equipment", lambda t: SC.TRANSIENT)
    assert EW.search_equipment_wikipedia("Panther") is None
    assert calls == []


def test_fetch_noaa_data_transient_guard(monkeypatch):
    import src.enrichment.noaa_weather as N

    calls = []
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result", lambda s, q, r: calls.append((q, r))
    )
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda s, q: None)
    monkeypatch.setattr(N, "_get", lambda *a, **k: SC.TRANSIENT)  # data query transient
    out = N.fetch_noaa_weather({"id": "GHCND:X", "name": "n"}, "1944-12-16", "tok")
    assert out is None
    assert calls == []  # no negative-cache on transient data fetch
