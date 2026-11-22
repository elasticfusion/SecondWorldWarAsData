"""Task 1.2f, first slice (owner 2026-11-18): an external service that is down must not mark work
done or replace it with a guess; the run picks up from where it stopped.

- OpenSERP down at a step's start or failing mid-run: the whole phase stops (owner decision
  D8c), exit 81, the book is deferred (connectivity) and resumes.
- A web page that could not be fetched transiently (walled / rate-limited / no archive) is not
  cached as REJECT for 90 days; only a confirmed-dead page is.
- archive.org not answering is not cached as "not on archive.org" for 7 days.
- Nominatim down: no LLM-guessed coordinate (owner decision D8d); after a streak of transient
  failures geocoding stops for the run and alerts; nothing is stamped.
"""

import json
from unittest import mock

import pytest

from src.utils import grok_models, stop_cause

# --- OpenSERP (D8c) -----------------------------------------------------------------------------


def test_openserp_down_at_the_start_stops_the_phase_for_connectivity(monkeypatch):
    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr(oe, "_openserp_reachable", lambda url: False)
    with pytest.raises(grok_models.ServiceUnreachable):
        oe.enrich_places_with_openserp(mock.MagicMock(), "http://serp", object())

    def main():
        oe._reset_and_check_reachable("http://serp")

    assert stop_cause.run_main(main) == stop_cause.EXIT_CONNECTIVITY


def test_openserp_failing_mid_run_stops_the_phase(monkeypatch):
    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("time.sleep", lambda *_: None)
    oe.reset_circuit()
    monkeypatch.setattr(
        "src.utils.fetch_provenance.recorded_fetch",
        lambda *a, **k: mock.Mock(status_code=503, json=lambda: {}),
    )
    monkeypatch.setattr(oe, "_openserp_reachable", lambda url: False)
    with pytest.raises(grok_models.ServiceUnreachable):
        for n in range(oe._CIRCUIT_BREAKER_THRESHOLD):
            oe._search_openserp(f"q{n}", "http://serp")
    # once found down, every later search stops too (nothing is searched and stamped)
    with pytest.raises(grok_models.ServiceUnreachable):
        oe._search_openserp("later", "http://serp")


def test_failing_queries_on_a_healthy_openserp_do_not_stop_the_phase(monkeypatch):
    """Security review: five bad queries (odd entity names from a book) are per-query failures
    when OpenSERP itself answers its health check."""
    import src.enrichment.openserp_enrichment as oe

    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("time.sleep", lambda *_: None)
    oe.reset_circuit()
    monkeypatch.setattr(
        "src.utils.fetch_provenance.recorded_fetch",
        lambda *a, **k: mock.Mock(status_code=422, json=lambda: {}),
    )
    monkeypatch.setattr(oe, "_openserp_reachable", lambda url: True)
    for n in range(3 * oe._CIRCUIT_BREAKER_THRESHOLD):
        assert oe._search_openserp(f"q{n}", "http://serp") == []
    assert not oe._breaker_is_open()


def test_the_stop_is_a_connectivity_stop_like_a_grok_outage():
    assert issubclass(grok_models.ServiceUnreachable, grok_models.GrokUnreachable)


# --- page fetch -----------------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["BLOCKED_NO_ARCHIVE", "RATE_LIMITED"])
def test_a_transient_page_fetch_is_not_a_90_day_reject(monkeypatch, source):
    import src.enrichment.openserp_enrichment as oe
    from src.utils import url_fetch

    monkeypatch.setattr(
        url_fetch, "fetch", lambda url: mock.Mock(source=getattr(url_fetch, source))
    )
    assert oe._summarize_url_page("http://a", "ctx", object()) == oe._RETRY_SENTINEL


@pytest.mark.parametrize("source", ["BLOCKED_NO_ARCHIVE", "RATE_LIMITED"])
def test_a_walled_page_is_not_fetched_or_verified_again_for_an_hour(
    monkeypatch, tmp_path, source
):
    """Task 1.2f: a short negative TTL, so other entities that find the same URL in this run
    do not re-fetch it (or pay for the Grok relevance check) while the wall lasts."""
    import time

    import src.enrichment.openserp_enrichment as oe
    from src.utils import search_cache, url_fetch

    backend = search_cache._LocalBackend(tmp_path)
    monkeypatch.setattr(search_cache, "_get_backend", lambda: backend)
    fetches, verifies = [], []
    monkeypatch.setattr(
        url_fetch,
        "fetch",
        lambda url: fetches.append(url) or mock.Mock(source=getattr(url_fetch, source)),
    )
    monkeypatch.setattr(
        oe, "_verify_result", lambda *a, **k: verifies.append(1) or True
    )
    for _ in range(3):
        assert oe.process_positive_url("http://a", "t", "s", "ctx", object()) is None
    assert fetches == ["http://a"] and verifies == [1]
    assert oe._url_verdict_cached("http://a") == oe._RETRY_VERDICT
    later = time.time() + oe.RETRY_LATER_HOURS * 3600 + 5
    monkeypatch.setattr(search_cache.time, "time", lambda: later)
    assert (
        oe._url_verdict_cached("http://a") is None
    )  # retried after the hour, never REJECT


def test_a_dead_page_is_a_negative(monkeypatch):
    import src.enrichment.openserp_enrichment as oe
    from src.utils import url_fetch

    monkeypatch.setattr(
        url_fetch, "fetch", lambda url: mock.Mock(source=url_fetch.DEAD)
    )
    assert oe._summarize_url_page("http://a", "ctx", object()) is None


# --- archive.org ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [mock.Mock(status_code=503), mock.Mock(side_effect=OSError("no route"))],
)
def test_an_archive_org_outage_is_not_cached_as_not_found(monkeypatch, response):
    from src.extraction import supplemental_search as ss

    cached = []
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda *a: None)
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result",
        lambda source, key, result, negative_days=None: cached.append(result),
    )
    if isinstance(response.side_effect, Exception):
        fetch = mock.Mock(side_effect=response.side_effect)
    else:
        fetch = mock.Mock(return_value=response)
    monkeypatch.setattr("src.utils.fetch_provenance.recorded_fetch", fetch)
    assert ss.search_archive_org("A Real Book Title About Patton") is None
    from src.utils.search_cache import TRANSIENT

    assert cached == [TRANSIENT]  # cache_result never stores TRANSIENT


def test_a_genuine_archive_org_miss_is_still_cached(monkeypatch):
    from src.extraction import supplemental_search as ss

    cached = []
    monkeypatch.setattr("src.utils.search_cache.get_cached", lambda *a: None)
    monkeypatch.setattr(
        "src.utils.search_cache.cache_result",
        lambda source, key, result, negative_days=None: cached.append(result),
    )
    monkeypatch.setattr(
        "src.utils.fetch_provenance.recorded_fetch",
        lambda *a, **k: mock.Mock(
            status_code=200, json=lambda: {"response": {"docs": []}}
        ),
    )
    assert ss.search_archive_org("A Real Book Title About Patton") is None
    assert cached == [None]  # a real miss: negative-cached


# --- Nominatim (D8d) ------------------------------------------------------------------------------


def test_a_nominatim_outage_stops_geocoding_for_the_run(tmp_path, monkeypatch):
    from src.enrichment import places_grok_geocode as pg

    for n in range(8):
        (tmp_path / f"p{n}.json").write_text(json.dumps({"name": f"Town{n}"}))
    calls = []

    def down(name, place, client):
        calls.append(name)
        return pg.GeocodeResult(found=False, transient=True, source="nominatim")

    alerts = []
    monkeypatch.setattr(
        "src.utils.observe.observe_error", lambda s, e, **k: alerts.append(e)
    )
    pg.geocode_places_dir(tmp_path, None, write=True, geocoder=down)
    assert len(calls) == pg.GEOCODER_DOWN_STREAK  # stopped, not 8
    calls.clear()
    alerts.clear()

    def raising(name, place, client):
        calls.append(name)
        raise ConnectionError("no route")

    pg.geocode_places_dir(tmp_path, None, write=True, geocoder=raising, force=True)
    assert len(calls) == pg.GEOCODER_DOWN_STREAK and alerts == ["geocoder_unavailable"]
    assert alerts == ["geocoder_unavailable"]
    for n in range(8):  # nothing stamped as not_found: all retried next run
        place = json.loads((tmp_path / f"p{n}.json").read_text())
        assert place.get("enrichment_status") in (None, pg._STATUS_ERROR)


def test_openserp_absent_from_config_is_off(monkeypatch):
    import os

    os.environ.setdefault("S3_BUCKET", "test-bucket")
    import ecs_entrypoint as ee

    monkeypatch.setattr(ee, "_config_or_empty", lambda: {})
    assert ee._openserp_switched_off() is True  # same default as phase3_enrich_data


def test_the_worker_looks_again_after_a_service_stop(monkeypatch):
    import openserp_worker as w
    import src.enrichment.openserp_enrichment as oe

    resets = []
    monkeypatch.setattr(w.time, "sleep", lambda *_: None)
    monkeypatch.setattr(w, "_safe_entity_path", lambda *a: True)
    monkeypatch.setattr(oe, "reset_circuit", lambda: resets.append(1))
    monkeypatch.setattr(
        oe,
        "enrich_one_place",
        mock.Mock(side_effect=grok_models.ServiceUnreachable("down")),
    )
    storage = mock.Mock(
        read_json=lambda p: {"name": "x", "PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW"}
    )
    out = w.process_message(
        {"entity_type": "places", "entity_path": "places/x.json"},
        storage,
        "http://serp",
        object(),
    )
    assert out == "defer" and resets == [1]


# --- watch() follows work into thread pools (owner 2026-11-20) -----------------------------


def test_an_outage_in_a_pool_thread_lands_in_the_submitters_watch():
    from concurrent.futures import ThreadPoolExecutor

    from src.utils import service_outage as so

    with so.watch() as seen:
        with ThreadPoolExecutor(max_workers=2) as plain:
            plain.submit(so.failed, "gutendex", "search", "x").result()
        assert seen == []  # a plain pool loses it (the gap)
        with so.OutagePool(max_workers=4) as pool:
            list(
                pool.map(
                    lambda s: so.failed(s, "search", "x"), ["nara", "gutendex", "nara"]
                )
            )
    assert sorted(seen) == ["gutendex", "nara"]  # once each, from any thread


def test_carry_outside_a_watch_changes_nothing():
    from src.utils import service_outage as so

    assert so.carry(lambda: so._scope.get())() is None


def test_an_outage_in_a_nested_watch_reaches_the_outer_one():
    from src.utils import service_outage as so

    with so.watch() as outer:
        with so.watch() as inner:
            so.failed("archive_org", "search", "x")
        assert inner == ["archive_org"]
        so.failed("archive_org", "search", "y")
    assert outer == ["archive_org"]  # once
