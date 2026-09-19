"""Geocode place names via the Nominatim (OpenStreetMap) API.

Nominatim resolves a place name against the authoritative OSM gazetteer,
returning real coordinates, a country, and a match importance we map to a
confidence. This is preferred over an LLM for genuine modern settlements/rivers/
forests: the coordinates come from a database entry, not a model's memory (which
tends to anchor a vague name to the nearest famous city).

It is deliberately *not* a fallback for everything: historical, OCR-garbled, or
military-feature names ("Hill 401", "Aachen Gap") have no OSM entry and simply
return no match here — those are left to the LLM geocoder in the cascade.

Usage-policy compliance (https://operations.osmfoundation.org/policies/nominatim/):

* A descriptive ``User-Agent`` is sent (required).
* Requests are rate-limited to <= 1/second (``_MIN_INTERVAL``).
* Results are cached on disk so a re-run makes no repeat calls.

This module reuses the project's pooled ``requests`` session. It returns the
same :class:`~src.enrichment.places_grok_geocode.GeocodeResult` shape as the LLM
geocoder so both share the cascade's write-back/report/idempotency machinery.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from src.enrichment.places_grok_geocode import GeocodeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://nominatim.openstreetmap.org/search"
# Required by Nominatim policy — identify the application and a contact.
_USER_AGENT = "SecondWorldWarAsData/1.0 (historical OOB place geocoding)"
# Nominatim allows at most 1 request/second; keep a safe margin.
_MIN_INTERVAL = 1.1
# OSM "importance" (0..1) at/above which we treat a match as confident enough to
# write; below this the match is returned with found=True but low confidence so
# the caller flags it for review rather than trusting it.
_IMPORTANCE_OK = 0.35

# ETO countries we expect; a match outside these is still returned but noted.
_ETO_COUNTRIES = frozenset(
    {"france", "germany", "belgium", "netherlands", "luxembourg", "united kingdom"}
)

_rate_lock = threading.Lock()
_last_call = [0.0]


def _throttle() -> None:
    """Block as needed so calls are spaced >= _MIN_INTERVAL apart (policy)."""
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def _cache_path(cache_dir: Path, name: str) -> Path:
    """Return the on-disk cache path for a place name's Nominatim result."""
    import hashlib

    digest = hashlib.sha256(name.lower().encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{digest}.json"


def _query_nominatim(name: str, session: Any) -> Optional[dict]:
    """Call Nominatim for ``name``; return the top raw result dict or None."""
    _throttle()
    resp = session.get(
        _ENDPOINT,
        params={
            "q": name,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
        },
        headers={"User-Agent": _USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    return results[0] if results else None


def _to_result(raw: Optional[dict]) -> GeocodeResult:
    """Convert a Nominatim result dict into a GeocodeResult."""
    if not raw:
        return GeocodeResult(
            found=False, confidence=0.0, note="no OSM match", source="osm"
        )
    try:
        lat = float(raw["lat"])
        lon = float(raw["lon"])
    except (KeyError, TypeError, ValueError):
        return GeocodeResult(
            found=False, confidence=0.0, note="malformed OSM result", source="osm"
        )

    importance = float(raw.get("importance", 0.0) or 0.0)
    country = (raw.get("address", {}) or {}).get("country")
    # Map OSM importance to a confidence; a below-threshold match is kept but
    # marked low so the caller flags it.
    confidence = round(min(0.95, max(0.4, importance + 0.3)), 2)
    note = None
    if importance < _IMPORTANCE_OK:
        confidence = 0.4
        note = f"low OSM importance ({importance:.2f})"
    elif country and country.lower() not in _ETO_COUNTRIES:
        note = f"OSM country '{country}' outside primary theater — verify"
    return GeocodeResult(
        found=True,
        latitude=lat,
        longitude=lon,
        country=country,
        confidence=confidence,
        note=note,
        source="osm",
    )


def make_nominatim_geocoder(cache_dir: Path, session: Any = None):
    """Return a ``geocode_place(name, place, _client)`` callable using Nominatim.

    The returned callable matches the LLM geocoder's signature so it drops into
    the cascade. ``cache_dir`` stores per-name JSON results (policy-friendly:
    a re-run makes no repeat network calls). ``session`` defaults to the
    project's pooled requests session.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    if session is None:
        from src.utils.http_pool import get_session

        session = get_session()

    def geocode_place(name: str, _place: dict, _client: Any = None) -> GeocodeResult:
        cache_file = _cache_path(cache_dir, name)
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                return GeocodeResult.model_validate(cached)
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        try:
            raw = _query_nominatim(name, session)
        except Exception as exc:  # noqa: BLE001 - record as a miss, keep batch alive
            logger.warning("Nominatim query failed for %s: %s", name, exc)
            return GeocodeResult(
                found=False, confidence=0.0, note=f"error: {exc}", source="osm"
            )
        result = _to_result(raw)
        try:
            cache_file.write_text(result.model_dump_json(), encoding="utf-8")
        except OSError:
            pass
        return result

    return geocode_place
