"""Verify a candidate coordinate against real terrain elevation.

WWII ETO "Hill N" / "Côte N" / "Height N" designations are the summit elevation
in *meters*, read from the metric French/German source maps (confirmed against
ground-truth features: Mortain's Hill 314 sits on ~318 m of terrain, St-Lô's
Hill 192 on ~167 m — a 3x mismatch if read as feet). This module checks whether
the terrain at a candidate coordinate matches the designation, which:

* confirms a good geocode (terrain ≈ N meters), and
* catches a bad one — e.g. a same-numbered hill on the wrong continent
  ("Hill 401" geocoded to New Hampshire) whose terrain won't match, or a pin
  dropped in a valley.

Meters is the primary interpretation; a feet reading is also computed so an
oddity can be reported rather than silently forcing meters. Uses the free
Open-Elevation API (place coordinates only; same low-risk external-call class as
Nominatim). Results are cached on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.open-elevation.com/api/v1/lookup"
# Terrain within this many meters of the designation counts as a match. The
# check is deliberately lenient and asymmetric: a "Hill N" designation is the
# *summit* elevation, but a geocoded point rarely lands exactly on the peak, and
# DEM resolution/rounding add error — so the query point is typically somewhat
# *lower* than N. We therefore allow terrain to sit well below the designation,
# and less far above it.
_TOLERANCE_BELOW_M = 90.0  # terrain may be up to this far below the designation
_TOLERANCE_ABOVE_M = 45.0  # ...and less far above (summit is the local max)
# Feet->meters factor, to test the alternate interpretation.
_FT_PER_M = 3.281


@dataclass
class ElevationCheck:
    """Outcome of checking a designation against terrain elevation."""

    terrain_m: Optional[float]
    designation: int
    fits_meters: bool
    fits_feet: bool
    note: str

    @property
    def verified(self) -> bool:
        """True if the designation matches terrain under the meters reading."""
        return self.fits_meters


def _cache_path(cache_dir: Path, lat: float, lon: float) -> Path:
    key = hashlib.sha256(f"{lat:.5f},{lon:.5f}".encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{key}.json"


def terrain_elevation(
    lat: float, lon: float, cache_dir: Path, session: Any = None
) -> Optional[float]:
    """Return terrain elevation (m) at a coordinate, or None on failure.

    Cached on disk by rounded coordinate; a failed/again-missing lookup returns
    None so the caller can proceed (verification simply becomes unavailable).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir, lat, lon)
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8")).get("elevation")
        except (OSError, json.JSONDecodeError):
            pass
    if session is None:
        from src.utils.http_pool import get_session

        session = get_session()
    try:
        resp = session.post(
            _ENDPOINT,
            json={"locations": [{"latitude": lat, "longitude": lon}]},
            timeout=30,
        )
        resp.raise_for_status()
        elevation = resp.json()["results"][0]["elevation"]
    except Exception as exc:  # noqa: BLE001 - unavailable verification, not fatal
        logger.warning("Elevation lookup failed for %s,%s: %s", lat, lon, exc)
        return None
    try:
        cache_file.write_text(json.dumps({"elevation": elevation}), encoding="utf-8")
    except OSError:
        pass
    return elevation


def check_designation(
    designation: int, lat: float, lon: float, cache_dir: Path, session: Any = None
) -> ElevationCheck:
    """Check whether terrain at (lat, lon) matches a height ``designation`` (m).

    Meters is primary; the feet reading is also evaluated so a rare imperial
    case (or a coordinate error) surfaces in the note instead of being masked.
    """
    terrain = terrain_elevation(lat, lon, cache_dir, session)
    if terrain is None:
        return ElevationCheck(
            terrain_m=None,
            designation=designation,
            fits_meters=False,
            fits_feet=False,
            note="terrain elevation unavailable — not verified",
        )
    fits_m = _within_tolerance(designation, terrain)
    fits_ft = _within_tolerance(designation / _FT_PER_M, terrain)
    note = _describe(designation, terrain, fits_m, fits_ft)
    return ElevationCheck(
        terrain_m=round(terrain, 1),
        designation=designation,
        fits_meters=fits_m,
        fits_feet=fits_ft,
        note=note,
    )


def _within_tolerance(expected_summit: float, terrain: float) -> bool:
    """True if terrain is consistent with a summit of ``expected_summit`` meters.

    Asymmetric: terrain may be well below the summit (the query point is on a
    slope, not the peak) but only modestly above it.
    """
    delta = terrain - expected_summit  # positive => terrain higher than summit
    if delta >= 0:
        return delta <= _TOLERANCE_ABOVE_M
    return -delta <= _TOLERANCE_BELOW_M


def _describe(designation: int, terrain: float, fits_m: bool, fits_ft: bool) -> str:
    """Build a human note describing the elevation fit."""
    if fits_m:
        return f"terrain {terrain:.0f} m matches designation {designation} (meters)"
    if fits_ft:
        return (
            f"terrain {terrain:.0f} m matches designation {designation} only as FEET "
            "— unusual for ETO; verify"
        )
    return (
        f"terrain {terrain:.0f} m does not match designation {designation} in meters "
        f"or feet — probable wrong location; verify"
    )
