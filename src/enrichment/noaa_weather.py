"""NOAA CDO API integration for weather enrichment.

Fetches observed weather data from NOAA's Climate Data Online (CDO) API
to supplement/validate Open-Meteo modeled data.

API limits: 5 requests/second, 10,000 requests/day.
"""

import json
import logging
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.utils.http_pool import get_session

logger = logging.getLogger(__name__)

BASE_URL = "https://www.ncei.noaa.gov/cdo-web/api/v2"
DATASET = "GHCND"
# Map NOAA GHCND daily element codes -> our schema fields (the standard daily set:
# AWND/PRCP/SNOW/SNWD/TMAX/TMIN/TAVG/WDF2/WDF5/WSF2/WSF5). Only real GHCND codes.
DATATYPE_MAP = {
    "TMAX": "temperature_high_c",
    "TMIN": "temperature_low_c",
    "TAVG": "temperature_avg_c",
    "PRCP": "precipitation_mm",
    "SNOW": "snowfall_mm",
    "SNWD": "snow_depth_mm",
    "AWND": "wind_speed_ms",
    "WSF2": "wind_gust_fastest2min_ms",
    "WSF5": "wind_gust_fastest5sec_ms",
    "WDF2": "wind_dir_fastest2min_deg",
    "WDF5": "wind_dir_fastest5sec_deg",
}

_last_request_time = 0.0
_request_count = 0
# H3: guard the rate-limiter state so it is correct if NOAA enrichment ever runs
# under the enrichment thread pool / multi-doc (check-then-sleep-then-update must
# be atomic or concurrent callers under-throttle).
_rate_lock = threading.Lock()


def _rate_limit():
    """Enforce 5 requests/second limit."""
    global _last_request_time, _request_count
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < 0.2:  # 5 req/sec = 200ms between requests
            time.sleep(0.2 - elapsed)
        _last_request_time = time.time()
        _request_count += 1


def _get(endpoint: str, token: str, params: Dict) -> Optional[Dict]:
    """Make rate-limited GET request to NOAA CDO API."""
    _rate_limit()
    try:
        session = get_session()
        resp = session.get(
            f"{BASE_URL}/{endpoint}",
            headers={"token": token},
            params=params,
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            logger.warning("NOAA rate limit hit, pausing 60s")
            time.sleep(60)
            return None
        logger.debug("NOAA %s returned %d", endpoint, resp.status_code)
    except Exception as e:
        logger.debug("NOAA request failed: %s", e)
    return None


def _station_from_results(data: Optional[Dict]) -> Optional[Dict[str, Any]]:
    """Pick the first usable station from a CDO stations response, keeping its
    resolved provenance (code + human-readable name + coordinates)."""
    if not data or not data.get("results"):
        return None
    r = data["results"][0]
    if not r.get("id"):
        return None
    return {
        "id": r["id"],
        "name": r.get("name"),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
    }


def _search_stations_box(lat: float, lon: float, half: float, date: str, token: str):
    """Query the CDO stations endpoint within a +/- `half` degree box."""
    extent = f"{lat-half},{lon-half},{lat+half},{lon+half}"
    return _get(
        "stations",
        token,
        {
            "datasetid": DATASET,
            "extent": extent,
            "startdate": date,
            "enddate": date,
            "limit": 5,
        },
    )


def find_nearest_station(
    lat: float, lon: float, date: str, token: str
) -> Optional[Dict[str, Any]]:
    """Find the nearest GHCND station with data for the date.

    Returns the resolved station dict {id, name, latitude, longitude} (NOT just the
    code) so St.-Vith-sourced-from-Antwerp records can preserve BOTH the GHCND code
    and the human-readable station place. Cached as JSON.
    """
    from src.utils.search_cache import cache_result, get_cached

    cache_key = f"{lat:.1f},{lon:.1f},{date[:7]}"
    cached = get_cached("noaa_station", cache_key)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return json.loads(cached)

    # ~50km box, then broaden to ~100km.
    station = _station_from_results(
        _search_stations_box(lat, lon, 0.5, date, token)
    ) or _station_from_results(_search_stations_box(lat, lon, 1.0, date, token))

    if station:
        cache_result("noaa_station", cache_key, json.dumps(station))
        return station

    cache_result("noaa_station", cache_key, None)
    return None


def fetch_noaa_weather(
    station: Any,
    date: str,
    token: str,
    place_lat: Optional[float] = None,
    place_lon: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch daily weather observations from NOAA for a station and date.

    `station` may be the resolved station dict ({id, name, latitude, longitude}) or a
    bare id string (back-compat). When the requested place coordinates are provided, the
    straight-line distance from the place to the observing station is recorded — so a
    St.-Vith record sourced from an Antwerp station carries BOTH the GHCND code and the
    resolved station place + how far away it is.
    """
    from src.utils.search_cache import cache_result, get_cached

    if isinstance(station, dict):
        station_id = station.get("id", "")
        station_name = station.get("name")
        station_lat = station.get("latitude")
        station_lon = station.get("longitude")
    else:
        station_id = station or ""
        station_name = None
        station_lat = None
        station_lon = None

    cache_key = f"{station_id}:{date}"
    cached = get_cached("noaa_data", cache_key)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return json.loads(cached)

    data = _get(
        "data",
        token,
        {
            "datasetid": DATASET,
            "stationid": station_id,
            "startdate": date,
            "enddate": date,
            "units": "metric",
            # Do NOT restrict datatypeid: absorb EVERY element the station reported
            # that day (named ones get canonical fields, all are kept in raw_elements).
            "limit": 1000,
        },
    )
    if not data or not data.get("results"):
        cache_result("noaa_data", cache_key, None)
        return None

    obs = {}
    raw_elements: Dict[str, Any] = {}
    for r in data["results"]:
        dtype = r.get("datatype", "")
        value = r.get("value")
        # Named canonical field for the common/queryable elements...
        if dtype in DATATYPE_MAP:
            obs[DATATYPE_MAP[dtype]] = value
        # ...AND absorb EVERY element NOAA returns (nothing dropped), keyed by its
        # raw GHCND datatype code. Elements we don't pre-map are still preserved here.
        if dtype:
            raw_elements[dtype] = value
    obs["raw_elements"] = raw_elements
    obs["station_id"] = station_id  # GHCND code, preserved
    obs["station_name"] = station_name  # resolved human-readable place
    obs["station_latitude"] = station_lat
    obs["station_longitude"] = station_lon
    # Distance from the REQUESTED place to the observing station (shared geo util).
    from src.utils.geo import haversine_km_opt

    dist = haversine_km_opt(place_lat, place_lon, station_lat, station_lon)
    obs["station_distance_km"] = round(dist, 1) if dist is not None else None
    obs["source"] = "noaa_cdo"
    obs["source_url"] = (
        f"https://www.ncei.noaa.gov/cdo-web/api/v2/data?datasetid=GHCND&stationid={station_id}&startdate={date}&enddate={date}"
    )
    obs["data_type"] = "observed"

    cache_result("noaa_data", cache_key, json.dumps(obs))
    return obs


def enrich_weather_with_noaa(weather_dir: Path, token: str, max_items: int = 0) -> int:
    """Enrich weather files with NOAA observed data. Returns count enriched."""
    if not token:
        return 0

    enriched = 0
    for f in sorted(weather_dir.glob("*.json")):
        if f.name == "index.json":
            continue
        if max_items and enriched >= max_items:
            break

        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Skip if already has NOAA data
        if data.get("noaa_observed"):
            continue

        loc = data.get("location", {})
        lat = loc.get("latitude", 0.0)
        lon = loc.get("longitude", 0.0)
        if lat == 0.0 and lon == 0.0:
            continue

        # Weather files store the date under "date" (not "date_start"); tolerate legacy.
        date = data.get("date") or data.get("date_start") or ""
        if not date or date < "1940-01-01":
            continue

        station = find_nearest_station(lat, lon, date, token)
        if not station:
            continue

        obs = fetch_noaa_weather(station, date, token, place_lat=lat, place_lon=lon)
        if obs:
            data["noaa_observed"] = obs
            from src.schemas import inject_metadata

            inject_metadata(data)
            f.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            enriched += 1
            logger.info("  NOAA enriched: %s (%s)", f.name, station.get("id"))

    logger.info(
        "NOAA weather enrichment: %d files enriched (API calls: %d)",
        enriched,
        _request_count,
    )
    return enriched
