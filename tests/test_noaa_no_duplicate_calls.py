"""Regression: NOAA data calls are not duplicated per (station, date).

Invariant (user requirement):
- "15 Dec 1944 St. Vith" triggers exactly ONE NOAA data fetch, even if multiple weather
  files/mentions exist for that place+date.
- "15 Dec 1944 Paris" is a SEPARATE, legitimate call (different station).

The guarantee rests on three layers, all exercised here through the real
enrich_weather_with_noaa path:
  1. per-file guard (skip files already carrying noaa_observed),
  2. the disk observation cache keyed {station_id}:{date},
  3. (upstream) weather dedup by {date}_pid_{PlaceID}.
This test isolates the cache to a temp dir and counts the HTTP data calls per station.
"""

import json
from collections import Counter

import src.enrichment.noaa_weather as nw


# lat/lon -> (station_id) for the test geography
_STATION_BY_LATLON = {
    (50.3, 6.1): "GHCND:STVITH",   # St. Vith (Belgium)
    (48.9, 2.4): "GHCND:PARIS",    # Paris
}


def _fake_get(endpoint, token, params):
    """Stand in for the NOAA CDO HTTP layer, routing by endpoint + lat/lon."""
    if endpoint == "stations":
        # extent is "lat-0.5,lon-0.5,lat+0.5,lon+0.5" -> recover the center to ~0.1
        parts = [float(x) for x in params["extent"].split(",")]
        clat = round((parts[0] + parts[2]) / 2, 1)
        clon = round((parts[1] + parts[3]) / 2, 1)
        sid = _STATION_BY_LATLON.get((clat, clon))
        return {"results": [{"id": sid}]} if sid else {"results": []}
    if endpoint == "data":
        _fake_get.data_calls[params["stationid"]] += 1
        return {
            "results": [
                {"datatype": "TMAX", "value": -1.0},
                {"datatype": "SNOW", "value": 80.0},
            ]
        }
    return None


def _write_weather(dirpath, wid, place, lat, lon):
    rec = {
        "WeatherID": wid,
        "date": "1944-12-15",
        "location": {"place_name": place, "latitude": lat, "longitude": lon},
        "source_type": "extracted",
    }
    (dirpath / f"{wid}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_noaa_called_once_per_station_date(tmp_path, monkeypatch):
    # Isolate the disk cache so prior runs don't poison the count
    monkeypatch.setattr(
        nw, "get_session", lambda: (_ for _ in ()).throw(AssertionError("no real HTTP"))
    )
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "_cache_backend", sc._LocalBackend(tmp_path / "cache"))

    _fake_get.data_calls = Counter()
    monkeypatch.setattr(nw, "_get", _fake_get)
    monkeypatch.setattr(nw, "_rate_limit", lambda: None)

    wdir = tmp_path / "weather"
    wdir.mkdir()
    # TWO St. Vith / 15 Dec files (simulating a dedup miss) + ONE Paris / 15 Dec
    _write_weather(wdir, "01STVITHAAAAAAAAAAAAAAAAAA", "St. Vith", 50.3, 6.1)
    _write_weather(wdir, "01STVITHBBBBBBBBBBBBBBBBBB", "Sankt Vith", 50.3, 6.1)
    _write_weather(wdir, "01PARISCCCCCCCCCCCCCCCCCCC", "Paris", 48.9, 2.4)

    enriched = nw.enrich_weather_with_noaa(wdir, token="tok")

    # All three files enriched...
    assert enriched == 3
    # ...but the St. Vith STATION's observations were fetched from NOAA exactly ONCE
    # (second St. Vith file is served from the {station}:{date} cache).
    assert _fake_get.data_calls["GHCND:STVITH"] == 1
    # Paris is a separate, legitimate single call.
    assert _fake_get.data_calls["GHCND:PARIS"] == 1


def test_noaa_skips_already_enriched_file(tmp_path, monkeypatch):
    """Per-file guard: a file already carrying noaa_observed triggers no new call."""
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "_cache_backend", sc._LocalBackend(tmp_path / "cache"))
    _fake_get.data_calls = Counter()
    monkeypatch.setattr(nw, "_get", _fake_get)
    monkeypatch.setattr(nw, "_rate_limit", lambda: None)

    wdir = tmp_path / "weather"
    wdir.mkdir()
    rec = {
        "WeatherID": "01ALREADYDONEAAAAAAAAAAAAA",
        "date": "1944-12-15",
        "location": {"place_name": "St. Vith", "latitude": 50.3, "longitude": 6.1},
        "source_type": "api_only",
        "noaa_observed": {"station_id": "GHCND:STVITH", "source": "noaa_cdo"},
    }
    (wdir / f"{rec['WeatherID']}.json").write_text(json.dumps(rec), encoding="utf-8")

    enriched = nw.enrich_weather_with_noaa(wdir, token="tok")
    assert enriched == 0
    assert sum(_fake_get.data_calls.values()) == 0
