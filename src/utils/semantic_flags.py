"""Semantic quality FLAGS (HIGH-2) — non-blocking content-plausibility checks.

The write guard validates record SHAPE and the referential checker validates CROSS-REFERENCES;
neither looks at whether a value is semantically plausible ("the guard confirms a record is
well-formed; nothing confirms it is informative or correct"). This module annotates records with
advisory ``quality_flags`` codes (impossible/placeholder coordinates, out-of-window dates,
unresolvable citations, ...) WITHOUT blocking the write or altering content — flag, never fabricate.

Central + fail-safe: one flag vocabulary, one entry point; any error returns the record unchanged.
Flags are additive observability (filterable). Forwarding flag outcomes to the statistical catcher
(an ``allow_flagged`` dimension, so a SYSTEMATIC cluster — e.g. a whole book geocoding to
impossible coordinates — raises a code-level alert) is NOT yet wired — tracked as future work
(HIGH-4).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

QUALITY_FLAGS_FIELD = "quality_flags"

# Coordinate plausibility is validated GLOBALLY — WWII was a global conflict and the corpus will
# grow worldwide, so there is NO theater/region bounding box (a regional box would flag correct
# places by design as the data expands). We only flag coordinates that are missing/placeholder or
# physically IMPOSSIBLE (a garbage geocode), never ones that are merely far from Europe.
_LAT_MIN, _LAT_MAX = -90.0, 90.0
_LON_MIN, _LON_MAX = -180.0, 180.0

# ETO campaign window for the DATE check (the corpus is currently the 1944–45 ETO campaign; this is
# a per-check window, independent of geography). ISO date strings compare lexicographically.
ETO_DATE_START = "1943-06-01"
ETO_DATE_END = "1945-12-31"

# Endnote fragments that cannot resolve to a real source.
_CITATION_STUBS = {"ibid", "ibid.", "op. cit.", "op.cit.", "loc. cit.", "idem", ""}
_BARE_NUMBER = re.compile(r"^\d{1,4}[.)]?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _geo_flags(record: Dict[str, Any]) -> List[str]:
    lat, lon = record.get("latitude"), record.get("longitude")
    if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
        return ["geo_missing_coords"]
    try:
        latf, lonf = float(lat), float(lon)
    except (TypeError, ValueError):
        return ["geo_missing_coords"]
    if not (_LAT_MIN <= latf <= _LAT_MAX and _LON_MIN <= lonf <= _LON_MAX):
        return ["geo_impossible_coords"]
    return []


def _iso_in_window(value: Any) -> Any:
    """Return True/False if value is a parseable ISO date (in/out of window), or None if it is not
    a concrete ISO date (approximate forms like 'summer-1944' are intentionally NOT flagged).
    """
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        return None
    day = value[:10]
    return ETO_DATE_START <= day <= ETO_DATE_END


def _date_flags(record: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    checked = False
    for field in (
        "resolved_earliest",
        "resolved_latest",
        "normalized_datetime",
        "date_start",
    ):
        verdict = _iso_in_window(record.get(field))
        if verdict is None:
            continue
        checked = True
        if verdict is False:
            out.append("date_out_of_window")
            break
    # A non-null date string that never parsed to ISO anywhere (and isn't an approximate form) is
    # worth flagging as unparseable — but only if SOMETHING date-like was present.
    if not checked:
        raw = record.get("date_start") or record.get("normalized_datetime")
        if isinstance(raw, str) and raw and not re.search(r"\d{4}", raw):
            out.append("date_unparseable")
    return out


def _citation_flags(record: Dict[str, Any]) -> List[str]:
    title = (record.get("title") or record.get("citation") or "").strip()
    low = title.lower()
    if low in _CITATION_STUBS or _BARE_NUMBER.match(low):
        return ["citation_stub"]
    has_archive = bool(record.get("archive_reference_number"))
    has_url = bool(record.get("resource_urls"))
    if not has_archive and not has_url:
        return ["citation_unresolvable"]
    return []


_VALIDATORS = {
    "places": _geo_flags,
    "dates": _date_flags,
    "bibliography": _citation_flags,
}

# The flag codes THIS module owns (recomputed + replaced on every annotate, so they clear once the
# underlying problem is fixed). Foreign codes on a record are preserved.
_OWNED_FLAGS = {
    "geo_missing_coords",
    "geo_impossible_coords",
    "date_out_of_window",
    "date_unparseable",
    "citation_stub",
    "citation_unresolvable",
}


def flags_for(entity: str, record: Dict[str, Any]) -> List[str]:
    """Pure: return the semantic quality-flag codes for a record (no mutation). Unknown entities
    and any internal error yield no flags (fail-safe — must never affect a write)."""
    try:
        validator = _VALIDATORS.get(entity)
        if validator is None or not isinstance(record, dict):
            return []
        return validator(record)
    except Exception as e:  # noqa: BLE001
        logger.debug("semantic flag check skipped for %s: %s", entity, e)
        return []


def annotate(entity: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute this module's semantic flags and attach them to ``quality_flags`` IN PLACE.

    REPLACE-not-union for the codes this module owns: a flag is CLEARED once the underlying problem
    is fixed (e.g. a place later geocoded to valid coords loses ``geo_missing_coords``), so the
    signal stays truthful. Flags set by OTHER sources (codes this module does not own) are
    preserved. Fail-safe + non-mutating of content: never raises, never touches content fields.
    """
    try:
        current = flags_for(entity, record)
        existing = record.get(QUALITY_FLAGS_FIELD) or []
        if not isinstance(existing, list):
            existing = []
        # Drop any of THIS module's codes from the existing set (stale), keep foreign codes, then
        # add the freshly-computed ones.
        kept = [f for f in existing if f not in _OWNED_FLAGS]
        merged = list(dict.fromkeys([*kept, *current]))  # dedup, preserve order
        if merged:
            record[QUALITY_FLAGS_FIELD] = merged
        elif QUALITY_FLAGS_FIELD in record:
            # All owned flags cleared and nothing else remains -> drop the empty field.
            del record[QUALITY_FLAGS_FIELD]
    except Exception as e:  # noqa: BLE001
        logger.debug("semantic flag annotate skipped for %s: %s", entity, e)
    return record
