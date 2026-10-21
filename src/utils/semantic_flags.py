"""Semantic quality FLAGS (HIGH-2) — non-blocking content-plausibility checks.

The write guard validates record SHAPE and the referential checker validates CROSS-REFERENCES;
neither looks at whether a value is semantically plausible ("the guard confirms a record is
well-formed; nothing confirms it is informative or correct"). This module annotates records with
advisory ``quality_flags`` codes (impossible/placeholder coordinates, out-of-window dates,
unresolvable citations, ...) WITHOUT blocking the write or altering content — flag, never fabricate.

Central + fail-safe: one flag vocabulary, one entry point; any error returns the record unchanged.
Flags are additive observability (filterable) AND are forwarded to the statistical catcher (HIGH-4:
`validation_stats.record_record_quality` records an ``allow_flagged`` datapoint per code), so a
SYSTEMATIC cluster — e.g. a whole book geocoding to impossible coordinates — raises a code-level
alert rather than a per-record decision.
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

# Date-plausibility window — the span of dates a record may legitimately carry. This is a WWII
# DATA corpus, not an ETO-only one, and WWII had a long antecedent/aftermath tail, so the window is
# generous AND configurable (never a hardcoded single-theater assumption):
#   - antecedents the corpus legitimately covers: Italian invasion of Abyssinia (1935), Spanish
#     Civil War (1936-39), Second Sino-Japanese War (1937), Anschluss/Sudetenland (1938), Battles
#     of Khalkhin Gol (1939) — all predate the "war started 1 Sep 1939" convention;
#   - aftermath: occupation, war-crimes trials, demobilization into the late 1940s.
# Default 1930-01-01 .. 1950-12-31 (wide enough to not false-flag the above). Override per corpus
# via config `processing.date_window: {start, end}` or env DATE_WINDOW_START / DATE_WINDOW_END.
_DEFAULT_DATE_WINDOW_START = "1930-01-01"
_DEFAULT_DATE_WINDOW_END = "1950-12-31"


def _window_from_cfg(cfg: dict, start, end) -> tuple:
    """Pick window values from a loaded config dict, env override first, then default."""
    win = cfg.get("processing", {}).get("date_window") or cfg.get("date_window") or {}
    return (
        start or win.get("start") or _DEFAULT_DATE_WINDOW_START,
        end or win.get("end") or _DEFAULT_DATE_WINDOW_END,
    )


def _load_date_window_from_config(start, end) -> tuple:
    """Resolve window from config, falling back to defaults. Fail-safe: any error returns the
    generous default (a validator must never crash a write)."""
    try:
        from src.utils.config import load_config

        cfg = load_config() or {}
        return _window_from_cfg(cfg, start, end)
    except Exception:  # noqa: BLE001
        return (
            start or _DEFAULT_DATE_WINDOW_START,
            end or _DEFAULT_DATE_WINDOW_END,
        )


def _load_date_window() -> tuple:
    """Resolve the plausibility window from env > config > default. Fail-safe: any error returns
    the generous default (a validator must never crash a write)."""
    import os

    start = os.environ.get("DATE_WINDOW_START")
    end = os.environ.get("DATE_WINDOW_END")
    if start and end:
        return start, end
    return _load_date_window_from_config(start, end)


DATE_WINDOW_START, DATE_WINDOW_END = _load_date_window()

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
    return DATE_WINDOW_START <= day <= DATE_WINDOW_END


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


def _citation_identified(record: Dict[str, Any]) -> bool:
    """Post-resolution: True if the record has ANY identity (archive ref, URL, canonical title,
    or reference_identified). A record positively IDENTIFIED is not "unresolvable" even without an
    online copy."""
    has_archive = bool(record.get("archive_reference_number"))
    has_url = bool(record.get("resource_urls"))
    sm = record.get("source_metadata") or {}
    return (
        has_archive
        or has_url
        or bool(sm.get("canonical_title"))
        or record.get("search_status") == "reference_identified"
    )


def _citation_flags(record: Dict[str, Any]) -> List[str]:
    _cit = record.get("citation")
    _cit_title = _cit.get("title") if isinstance(_cit, dict) else _cit
    title = (record.get("title") or _cit_title or "").strip()
    low = title.lower()
    if low in _CITATION_STUBS or _BARE_NUMBER.match(low):
        return ["citation_stub"]
    # citation_unresolvable is a POST-RESOLUTION signal. archive_reference_number and
    # resource_urls are populated by the Phase-3 resolver; a freshly-EXTRACTED record (written
    # at the Phase-2 perimeter, before resolution can have run) necessarily lacks both and is
    # merely NOT-YET-RESOLVED, not unresolvable. Flag only once resolution was ATTEMPTED —
    # signalled by a resolver-set ``search_status`` — and still produced no archive ref / URL.
    # (Without this guard every extracted citation trips the flag -> ~50% false-positive rate
    # that fired the data-quality alert; the condition evaluated a Phase-3 field at Phase-2 time.)
    if not record.get("search_status"):
        return []
    # A record positively IDENTIFIED is not "unresolvable" even without an online copy: a
    # biblio-note published work carries a canonical title/author, and reference_identified means
    # the record group / source is known. Only a post-resolution record with NO identity at all
    # (no archive ref, no URL, no canonical identity, not reference_identified) is unresolvable.
    if not _citation_identified(record):
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
