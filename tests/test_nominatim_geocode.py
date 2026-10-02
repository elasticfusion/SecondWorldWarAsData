"""Tests for the Nominatim geocoder and the geocoder cascade (no network)."""

import json
from pathlib import Path

from src.enrichment.nominatim_geocode import _to_result, make_nominatim_geocoder
from src.enrichment.places_grok_geocode import GeocodeResult, cascade_geocoder


def test_to_result_confident_match() -> None:
    raw = {
        "lat": "50.7766",
        "lon": "6.0834",
        "importance": 0.7,
        "address": {"country": "Germany"},
    }
    r = _to_result(raw)
    assert r.found is True
    assert r.latitude == 50.7766
    assert r.country == "Germany"
    assert r.confidence >= 0.9


def test_to_result_low_importance_flagged() -> None:
    raw = {"lat": "1.0", "lon": "2.0", "importance": 0.1, "address": {}}
    r = _to_result(raw)
    assert r.found is True
    assert r.confidence == 0.4  # below-threshold -> low confidence
    assert "low OSM importance" in r.note


def test_to_result_no_match() -> None:
    r = _to_result(None)
    assert r.found is False


def test_to_result_non_european_not_flagged() -> None:
    # The corpus spans ETO now but will add North Africa, the Pacific, and Naval
    # data — a non-European country must NOT be flagged "outside theater".
    raw = {
        "lat": "35.6",
        "lon": "139.7",
        "importance": 0.8,
        "address": {"country": "Japan"},
    }
    r = _to_result(raw)
    assert r.found is True
    assert r.country == "Japan"
    assert r.note is None  # no outside-theater flag anymore


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        # Policy: a descriptive User-Agent must be sent.
        assert headers and "User-Agent" in headers
        return _FakeResp(self._payload)


def test_nominatim_geocoder_caches(tmp_path: Path) -> None:
    session = _FakeSession(
        [
            {
                "lat": "50.78",
                "lon": "6.08",
                "importance": 0.7,
                "address": {"country": "Germany"},
            }
        ]
    )
    geocode = make_nominatim_geocoder(tmp_path / "cache", session=session)
    r1 = geocode("Aachen", {}, None)
    r2 = geocode("Aachen", {}, None)  # served from cache
    assert r1.latitude == 50.78 and r2.latitude == 50.78
    assert session.calls == 1  # second call hit the cache, not the network


class _QueryAwareSession:
    """Returns a hit only for specific query strings; records params seen."""

    def __init__(self, hits):
        self._hits = hits  # {query_lower: result_dict}
        self.params_seen = []

    def get(self, url, params=None, headers=None, timeout=None):
        assert headers and "User-Agent" in headers
        self.params_seen.append(params or {})
        q = (params or {}).get("q", "").lower()
        return _FakeResp([self._hits[q]] if q in self._hits else [])


def test_country_normalized_from_multilingual(tmp_path: Path) -> None:
    session = _FakeSession(
        [
            {
                "lat": "50.43",
                "lon": "6.30",
                "importance": 0.6,
                "address": {"country": "België / Belgique / Belgien"},
            }
        ]
    )
    geocode = make_nominatim_geocoder(tmp_path / "c", session=session)
    r = geocode("Rocherath", {"country": "Belgium"}, None)
    assert r.found and r.country == "België"  # first component, not the slash-string


def test_country_code_scoping_applied(tmp_path: Path) -> None:
    session = _QueryAwareSession(
        {
            "clervaux": {
                "lat": "50.05",
                "lon": "6.03",
                "importance": 0.6,
                "address": {"country": "Luxembourg"},
            }
        }
    )
    geocode = make_nominatim_geocoder(tmp_path / "c", session=session)
    geocode("Clervaux", {"country": "Luxembourg"}, None)
    assert session.params_seen[0].get("countrycodes") == "lu"
    assert session.params_seen[0].get("accept-language") == "en"


def test_country_code_scoping_skipped_when_unknown(tmp_path: Path) -> None:
    # Naval / unknown-country place -> no countrycodes (unscoped), still works.
    session = _QueryAwareSession(
        {
            "wake island": {
                "lat": "19.3",
                "lon": "166.6",
                "importance": 0.6,
                "address": {"country": "United States"},
            }
        }
    )
    geocode = make_nominatim_geocoder(tmp_path / "c", session=session)
    r = geocode("Wake Island", {}, None)
    assert r.found
    assert "countrycodes" not in session.params_seen[0]


def test_alias_translation_fallback(tmp_path: Path) -> None:
    # Primary (as written in the source doc) misses; a translated/historical
    # variant resolves. OSM only knows "Aachen"; the doc said "Aix-la-Chapelle".
    session = _QueryAwareSession(
        {
            "aachen": {
                "lat": "50.78",
                "lon": "6.08",
                "importance": 0.7,
                "address": {"country": "Germany"},
            }
        }
    )
    geocode = make_nominatim_geocoder(tmp_path / "c", session=session)
    place = {
        "country": "Germany",
        "historical_names": [{"name": "Aachen", "language": "German"}],
    }
    r = geocode("Aix-la-Chapelle", place, None)
    assert r.found and r.latitude == 50.78
    assert "variant 'Aachen'" in (r.note or "")


def test_cascade_prefers_first_confident() -> None:
    def hi(name, place, client):
        return GeocodeResult(found=True, latitude=1, longitude=2, confidence=0.9)

    def lo(name, place, client):
        return GeocodeResult(found=True, latitude=9, longitude=9, confidence=0.9)

    cascade = cascade_geocoder(hi, lo)
    r = cascade("X", {}, None)
    assert r.latitude == 1  # first confident result wins; lo never used


def test_cascade_falls_back_on_miss() -> None:
    def miss(name, place, client):
        return GeocodeResult(found=False, confidence=0.0)

    def hit(name, place, client):
        return GeocodeResult(found=True, latitude=5, longitude=6, confidence=0.8)

    cascade = cascade_geocoder(miss, hit)
    r = cascade("X", {}, None)
    assert r.found is True and r.latitude == 5


def test_cascade_returns_best_when_none_confident() -> None:
    def low1(name, place, client):
        return GeocodeResult(found=True, latitude=1, longitude=1, confidence=0.3)

    def low2(name, place, client):
        return GeocodeResult(found=True, latitude=2, longitude=2, confidence=0.45)

    cascade = cascade_geocoder(low1, low2)
    r = cascade("X", {}, None)
    # Neither is above threshold; the higher-confidence one is surfaced.
    assert r.confidence == 0.45
