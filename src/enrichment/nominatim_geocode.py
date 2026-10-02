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

# Country-name/alias -> ISO-3166-alpha2, used ONLY to optionally SCOPE a Nominatim
# query to a country when the place record gives a hint (disambiguates cross-country
# name collisions). This is an optional optimization, NOT a theater gate: a place
# whose country is not listed here simply gets an UNSCOPED search (still works). The
# project starts in the ETO but will ingest North Africa, the Pacific, and Naval
# data — extend this map as new theaters land; nothing breaks if it is incomplete.
# English country NAMES come from OSM directly via accept-language=en (see
# _query_nominatim), so no exhaustive localized-name map is needed for normalization.
_COUNTRY_TO_ISO = {
    # ETO (current focus)
    "france": "fr",
    "germany": "de",
    "deutschland": "de",
    "belgium": "be",
    "belgië": "be",
    "belgique": "be",
    "netherlands": "nl",
    "nederland": "nl",
    "luxembourg": "lu",
    "lëtzebuerg": "lu",
    "united kingdom": "gb",
    "england": "gb",
    "italy": "it",
    # North Africa (future)
    "tunisia": "tn",
    "algeria": "dz",
    "morocco": "ma",
    "libya": "ly",
    "egypt": "eg",
    # Pacific (future) — many ops are at sea / island groups; scope is best-effort
    "japan": "jp",
    "philippines": "ph",
    "australia": "au",
    "papua new guinea": "pg",
}


def _country_hint(place: dict) -> Optional[str]:
    """Extract a country hint from the place record (explicit country field, else
    the hierarchy), lowercased, if it maps to a known country code. Theater-agnostic:
    an unknown/absent country returns None → unscoped search (correct for naval/
    open-ocean places that have no country)."""
    cand = place.get("country") or ""
    if not cand:
        hierarchy = place.get("hierarchy") or ""
        text = (
            hierarchy if isinstance(hierarchy, str) else " ".join(map(str, hierarchy))
        )
        for known in _COUNTRY_TO_ISO:
            if known in text.lower():
                cand = known
                break
    cand = str(cand).strip().lower()
    return cand if cand in _COUNTRY_TO_ISO else None


def _normalize_country(country: Optional[str]) -> Optional[str]:
    """Defensive normalization of an OSM country name to English.

    Primary mechanism is accept-language=en on the request (works for ALL
    countries/theaters). This only cleans up a multilingual slash/semicolon
    string (e.g. a cached 'België / Belgique / Belgien') by taking the first
    component, and otherwise PASSES THROUGH unchanged — so non-mapped countries
    (Tunisia, Japan, ...) are never mangled."""
    if not country:
        return country
    import re

    for part in re.split(r"[/;,]", country):
        part = part.strip()
        if part:
            return part
    return country


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


def _query_nominatim(
    name: str, session: Any, country_code: Optional[str] = None
) -> Optional[dict]:
    """Call Nominatim for ``name``; return the top raw result dict or None.

    When ``country_code`` is given (from the place record's country/hierarchy
    context), the search is scoped to that country via ``countrycodes`` — this
    disambiguates names that collide across ETO countries without a second call.
    """
    _throttle()
    params = {
        "q": name,
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
        # Return English names for all countries/theaters (ETO, N. Africa, Pacific)
        # so country/place names are consistent without a per-language map.
        "accept-language": "en",
    }
    if country_code:
        params["countrycodes"] = country_code
    resp = session.get(
        _ENDPOINT,
        params=params,
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
    # Normalize OSM's (possibly localized) country name to English, e.g.
    # 'Deutschland' -> 'Germany', so downstream data is consistent.
    country = _normalize_country((raw.get("address", {}) or {}).get("country"))
    # Map OSM importance to a confidence; a below-threshold match is kept but
    # marked low so the caller flags it.
    confidence = round(min(0.95, max(0.4, importance + 0.3)), 2)
    note = None
    if importance < _IMPORTANCE_OK:
        confidence = 0.4
        note = f"low OSM importance ({importance:.2f})"
    # NB: no "outside theater" flag — the corpus spans ETO now but will add North
    # Africa, the Pacific, and Naval data, so a non-European country is normal.
    return GeocodeResult(
        found=True,
        latitude=lat,
        longitude=lon,
        country=country,
        confidence=confidence,
        note=note,
        source="osm",
    )


def _alias_names(place: dict, primary: str) -> list:
    """Translated / historical / alias name variants to try if the primary name
    misses in OSM. Source documents often use a translated or period form
    ('Aix-la-Chapelle'/'Aken' for Aachen; 'Weißenburg' for Wissembourg;
    'Le Havre' for 'Havre'), which OSM may resolve when the primary does not.
    De-duplicated, excludes the primary; order preserved (historical first)."""
    out: list = []
    seen = {primary.strip().lower()}
    for hn in place.get("historical_names") or []:
        nm = hn.get("name") if isinstance(hn, dict) else hn
        if nm and str(nm).strip().lower() not in seen:
            out.append(str(nm).strip())
            seen.add(str(nm).strip().lower())
    for al in place.get("aliases") or []:
        if al and str(al).strip().lower() not in seen:
            out.append(str(al).strip())
            seen.add(str(al).strip().lower())
    return out


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

    def _lookup(query: str, country_code: Optional[str]) -> GeocodeResult:
        """One cached Nominatim lookup for a single query string + optional scope."""
        cache_file = _cache_path(cache_dir, f"{query}|{country_code or ''}")
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                return GeocodeResult.model_validate(cached)
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        try:
            raw = _query_nominatim(query, session, country_code)
        except Exception as exc:  # noqa: BLE001 - record as a miss, keep batch alive
            logger.warning("Nominatim query failed for %s: %s", query, exc)
            return GeocodeResult(
                found=False, confidence=0.0, note=f"error: {exc}", source="osm"
            )
        result = _to_result(raw)
        try:
            cache_file.write_text(result.model_dump_json(), encoding="utf-8")
        except OSError:
            pass
        return result

    def geocode_place(name: str, _place: dict, _client: Any = None) -> GeocodeResult:
        # Context-aware: scope the OSM query to the place's country when the record
        # (or its hierarchy) gives one — disambiguates cross-country name
        # collisions. No hint -> unscoped (correct for naval/open-ocean places).
        place = _place if isinstance(_place, dict) else {}
        hint = _country_hint(place)
        country_code = _COUNTRY_TO_ISO.get(hint) if hint else None

        # Try the primary name first; on a miss, try translated/historical/alias
        # variants (source docs may use a translated or period place name).
        result = _lookup(name, country_code)
        if result.found:
            return result
        for alias in _alias_names(place, name):
            alt = _lookup(alias, country_code)
            if alt.found:
                if alt.note:
                    alt.note = f"matched via variant '{alias}'; {alt.note}"
                else:
                    alt.note = f"matched via variant '{alias}'"
                return alt
        return result  # the primary miss (keeps its note)

    return geocode_place
