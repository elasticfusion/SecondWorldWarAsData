"""Offline geo-enrichment and QA for extracted place records.

The place extractor emits coordinates the LLM produced (nested under
``coordinates``) and derives a ``bounding_box`` and ``map_urls`` from them. Over
a large corpus two gaps accumulate that can be closed *offline*, without any
network geocoding call:

* **Missing derived fields** — a geocoded place that lacks ``bounding_box`` or
  ``map_urls`` (e.g. an older extraction). These are pure functions of the
  coordinates, so they are recomputed deterministically.
* **Out-of-theater coordinates** — this corpus is the WWII European Theater
  (Western Front, ~1944-45). A coordinate far outside that theater is almost
  certainly an LLM geocoding error (e.g. a hill number resolved to the far side
  of the world). Such places are *flagged* for review, never moved — the same
  "verification, not correction" posture used elsewhere.

Places that have *no* coordinates but *do* have a name need a real geocoding
pass (LLM or gazetteer, which requires network/cost); this module does not do
that, but it produces a work-queue of exactly those names so the later pass is
targeted rather than blind.

This module is import-safe and side-effect-free until a caller invokes
:func:`enrich_places_dir`; callers decide when to persist.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Files in a places dir that are not individual place records.
_SKIP_FILES = frozenset(
    {
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "not_related.json",
        "review_queue.json",
        "related_groups_report.json",
        ".processed_events.json",
    }
)

# European Theater of Operations bounding window (generous): the Western Front
# and its rear areas span roughly Normandy to the Rhine and the UK to the Alps.
# A geocoded place outside this is treated as a probable geocoding error and
# flagged (not moved). Bounds are deliberately loose to avoid false positives.
ETO_LAT_MIN, ETO_LAT_MAX = 40.0, 56.0
ETO_LON_MIN, ETO_LON_MAX = -6.0, 16.0


@dataclass
class PlaceGeoReport:
    """Summary of an offline place geo-enrichment pass."""

    scanned: int = 0
    derived_fields_added: int = 0
    outliers_flagged: int = 0
    needs_geocoding: List[str] = field(default_factory=list)
    non_settlement: List[str] = field(default_factory=list)
    outliers: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "scanned": self.scanned,
            "derived_fields_added": self.derived_fields_added,
            "outliers_flagged": self.outliers_flagged,
            "needs_geocoding_count": len(self.needs_geocoding),
            "needs_geocoding": sorted(self.needs_geocoding),
            "non_settlement_count": len(self.non_settlement),
            "non_settlement": sorted(self.non_settlement),
            "outliers": self.outliers,
        }


def _bounding_box(lat: float, lon: float) -> Dict[str, float]:
    """Return a ~100km bounding box around a coordinate (matches extractor)."""
    return {
        "north": round(lat + 0.9, 4),
        "south": round(lat - 0.9, 4),
        "east": round(lon + 0.9, 4),
        "west": round(lon - 0.9, 4),
    }


def _map_urls(lat: float, lon: float) -> Dict[str, str]:
    """Return map-service URLs for a coordinate (matches extractor)."""
    return {
        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
        "openstreetmap": (
            f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12"
        ),
    }


def _coords(place: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Return (lat, lon) from the nested ``coordinates`` block, or (None, None)."""
    coords = place.get("coordinates") or {}
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    if lat in (None, 0, 0.0) and lon in (None, 0, 0.0):
        return None, None
    return lat, lon


def _place_name(place: Dict[str, Any]) -> Optional[str]:
    """Return the best available display name for a place, or None."""
    return (
        place.get("current_name")
        or place.get("place_name")
        or place.get("identified_as")
    )


def _is_out_of_theater(lat: float, lon: float) -> bool:
    """True if a coordinate falls outside the ETO window (probable error)."""
    return not (ETO_LAT_MIN <= lat <= ETO_LAT_MAX and ETO_LON_MIN <= lon <= ETO_LON_MAX)


def enrich_place(place: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Return (possibly-updated place, list of change tags).

    Recomputes missing ``bounding_box``/``map_urls`` from coordinates and adds a
    ``geo_review`` flag for out-of-theater coordinates. Never moves coordinates.
    Change tags: ``"derived"`` (added derived fields), ``"outlier"`` (flagged).
    """
    lat, lon = _coords(place)
    changes: List[str] = []
    if lat is None or lon is None:
        return place, changes

    if not place.get("bounding_box"):
        place["bounding_box"] = _bounding_box(lat, lon)
        changes.append("derived")
    if not place.get("map_urls"):
        place["map_urls"] = _map_urls(lat, lon)
        if "derived" not in changes:
            changes.append("derived")

    if _is_out_of_theater(lat, lon) and not place.get("geo_review"):
        place["geo_review"] = (
            f"coordinate ({lat}, {lon}) is outside the primary ETO window "
            f"[{ETO_LAT_MIN}..{ETO_LAT_MAX}, {ETO_LON_MIN}..{ETO_LON_MAX}] "
            "— may be a strategic/background location (correct) or a geocoding "
            "error; verify"
        )
        changes.append("outlier")
    return place, changes


# Tokens marking a name that is a military/relative area rather than a
# geocodable settlement (e.g. "26th Infantry Division sector"). These are
# reported separately so the real geocoding work-queue is not diluted.
_NON_SETTLEMENT_TOKENS = (
    "sector",
    "area",
    "division",
    "corps",
    "regiment",
    "battalion",
    "front",
    "flank",
    "line",
    "boundary",
)


def is_geocodable_name(name: str) -> bool:
    """True if a place name looks like a settlement/feature, not a unit area.

    Names like "5th Division area" or "XII Corps sector" are relative military
    areas without a fixed civilian coordinate, so they are not real geocoding
    targets; this lets the work-queue separate them from actual places.
    """
    low = name.lower()
    return not any(tok in low for tok in _NON_SETTLEMENT_TOKENS)


def enrich_places_dir(places_dir: Path, write: bool = False) -> PlaceGeoReport:
    """Run offline geo-enrichment over every place file in ``places_dir``.

    Args:
        places_dir: The places output directory (e.g. ``output/places/``).
        write: When True, persist changed place files in place (durable write);
            when False (default) compute the report without modifying files.

    Returns:
        A :class:`PlaceGeoReport` summarizing derived fields added, outliers
        flagged, and the names still needing a real geocoding pass.
    """
    report = PlaceGeoReport()
    if not places_dir.is_dir():
        return report

    for path in sorted(places_dir.glob("*.json")):
        if path.name in _SKIP_FILES:
            continue
        place = _load_place(path)
        if place is None:
            continue
        report.scanned += 1
        _process_place(path, place, report, write)

    logger.info(
        "Place geo-enrichment: scanned %d, derived-fields added %d, outliers "
        "flagged %d, needs-geocoding %d",
        report.scanned,
        report.derived_fields_added,
        report.outliers_flagged,
        len(report.needs_geocoding),
    )
    return report


def _load_place(path: Path) -> Optional[Dict[str, Any]]:
    """Load a place JSON file, or None if unreadable/not an object."""
    try:
        place = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return place if isinstance(place, dict) else None


def _process_place(
    path: Path, place: Dict[str, Any], report: PlaceGeoReport, write: bool
) -> None:
    """Enrich one place and fold its outcome into ``report`` (persist if write)."""
    lat, lon = _coords(place)
    if lat is None or lon is None:
        name = _place_name(place)
        if name:
            queue = (
                report.needs_geocoding
                if is_geocodable_name(name)
                else report.non_settlement
            )
            queue.append(name)
        return

    updated, changes = enrich_place(place)
    if "derived" in changes:
        report.derived_fields_added += 1
    if "outlier" in changes:
        report.outliers_flagged += 1
        report.outliers.append({"name": _place_name(updated), "lat": lat, "lon": lon})
    if changes and write:
        _write_place(path, updated)


def _write_place(path: Path, place: Dict[str, Any]) -> None:
    """Persist an updated place record via the durable writer."""
    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(path, place)


def write_geo_report(output_root: Path, report: PlaceGeoReport) -> Path:
    """Persist the geo-enrichment report under output/places/reports/.

    Written to a ``reports`` subdirectory (not the ``places`` entity dir) so the
    entity-field validator does not treat the report as a malformed place.
    """
    from src.utils.file_lock import write_json_with_lock

    out_path = output_root / "places" / "reports" / "geo_report.json"
    write_json_with_lock(out_path, report.to_dict())
    logger.info("Wrote place geo report to %s", out_path)
    return out_path


def _asdict_report(report: PlaceGeoReport) -> Dict[str, Any]:
    """Expose dataclass conversion for callers/tests."""
    return asdict(report)
