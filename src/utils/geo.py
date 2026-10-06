"""Shared geographic helpers.

Single home for great-circle distance so features (NOAA station-distance, place
deduplication, map features) share one tested implementation instead of each
re-deriving haversine.
"""

from math import asin, cos, radians, sin, sqrt
from typing import Optional

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometres."""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * EARTH_RADIUS_KM


def haversine_km_opt(
    lat1: Optional[float],
    lon1: Optional[float],
    lat2: Optional[float],
    lon2: Optional[float],
) -> Optional[float]:
    """haversine_km that tolerates missing coordinates (returns None if any is None)."""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    try:
        return haversine_km(float(lat1), float(lon1), float(lat2), float(lon2))
    except (ValueError, TypeError):
        return None
