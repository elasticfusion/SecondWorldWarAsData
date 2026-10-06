"""St. Vith sourced from an Antwerp station: preserve BOTH the GHCND code and the
resolved station place (name + coords), and quantify the substitution via distance."""

import jsonschema

import src.enrichment.noaa_weather as nw
from src.schemas.weather_output import WEATHER_OUTPUT_SCHEMA as S
from src.utils.geo import haversine_km, haversine_km_opt

UL = "01ABCDEFGH0123456789ABCDEF"

# St. Vith (BE) and Antwerp (BE) ~140 km apart
STVITH = (50.28, 6.13)
ANTWERP = (51.20, 4.47)


def test_geo_shared_util():
    assert haversine_km(50.0, 6.0, 50.0, 6.0) == 0.0
    assert 120 < haversine_km(*STVITH, *ANTWERP) < 160
    assert haversine_km_opt(None, 6.0, 50.0, 6.0) is None


def test_stvith_sourced_from_antwerp_preserves_code_and_place(monkeypatch):
    """Place is St. Vith but nearest station is Antwerp: record keeps the GHCND code,
    the resolved station name/coords, AND the distance from St. Vith to Antwerp."""
    import src.utils.search_cache as sc

    # no cache hits
    monkeypatch.setattr(sc, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *a, **k: None)
    monkeypatch.setattr(nw, "_rate_limit", lambda: None)

    def fake_get(endpoint, token, params):
        if endpoint == "data":
            return {
                "results": [
                    {"datatype": "TMAX", "value": -2.0},
                    {"datatype": "SNOW", "value": 81.3},
                ]
            }
        return None

    monkeypatch.setattr(nw, "_get", fake_get)

    station = {
        "id": "GHCND:BE000006447",
        "name": "ANTWERPEN/DEURNE, BE",
        "latitude": ANTWERP[0],
        "longitude": ANTWERP[1],
    }
    obs = nw.fetch_noaa_weather(
        station, "1944-12-15", "tok", place_lat=STVITH[0], place_lon=STVITH[1]
    )

    # BOTH preserved: the opaque code AND the resolved place
    assert obs["station_id"] == "GHCND:BE000006447"
    assert obs["station_name"] == "ANTWERPEN/DEURNE, BE"
    assert obs["station_latitude"] == ANTWERP[0]
    # substitution quantified: ~140 km, not null
    assert obs["station_distance_km"] is not None
    assert 120 < obs["station_distance_km"] < 160
    assert obs["snowfall_mm"] == 81.3

    rec = {
        "WeatherID": UL,
        "date": "1944-12-15",
        "location": {"place_name": "St. Vith"},
        "source_type": "api_only",
        "noaa_observed": obs,
    }
    jsonschema.validate(rec, S)


def test_find_nearest_station_returns_resolved_dict(monkeypatch):
    import src.utils.search_cache as sc

    monkeypatch.setattr(sc, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(sc, "cache_result", lambda *a, **k: None)
    monkeypatch.setattr(nw, "_rate_limit", lambda: None)
    monkeypatch.setattr(
        nw,
        "_get",
        lambda e, t, p: {
            "results": [
                {
                    "id": "GHCND:BE000006447",
                    "name": "ANTWERPEN/DEURNE, BE",
                    "latitude": ANTWERP[0],
                    "longitude": ANTWERP[1],
                }
            ]
        },
    )
    st = nw.find_nearest_station(*STVITH, "1944-12-15", "tok")
    assert st["id"] == "GHCND:BE000006447"
    assert st["name"] == "ANTWERPEN/DEURNE, BE"
