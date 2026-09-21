"""Elevation-and-context-aware geocoding for WWII named-height features.

A designation like "Hill 401" or "Côte 192" encodes the summit elevation in
meters (confirmed against ground-truth ETO hills). That number alone does not
locate the hill — many summits share an elevation — but combined with the
*proximity context* already in the place record (the event's nearby named towns
and ridges) it becomes locatable, and the elevation then serves as a
verification constraint.

Pipeline per height feature:

1. Recognize the feature and extract the elevation (meters) — ``Hill N``,
   ``Height N``, ``Côte/Cote N``, ``Pt/Point N`` (with an optional decimal).
2. Pull proximity anchors from the record's ``event_mentions`` (nearby places
   named in the sub-event / original text).
3. Ask Grok to locate a height of ~N meters *within that area*, returning
   coordinates and its reasoning.
4. Verify the candidate against real terrain elevation
   (:mod:`src.enrichment.elevation_verify`): terrain ≈ N meters confirms it;
   a gross mismatch (e.g. a same-numbered hill on another continent) is rejected
   and flagged.

Returns the shared :class:`GeocodeResult` so it composes with the geocoder
cascade. This is intended as the terrain-feature specialist, tried before the
generic Grok fallback for names OSM cannot resolve.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, List, Optional

from src.enrichment.elevation_verify import check_designation
from src.enrichment.places_grok_geocode import GeocodeResult, _parse_geocode_reply

logger = logging.getLogger(__name__)

# Named-height patterns; group 1 is the elevation (an optional decimal is
# ignored — the integer meters is what matters).
_HEIGHT_RE = re.compile(
    r"\b(?:hill|height|h[öo]he|c[ôo]te|cota|point|pt)\s+(\d{2,4})(?:\.\d+)?\b",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "You are a historical military geographer for the WWII European Theater "
    "(Western Front, 1944-1945). A 'Hill N' / 'Cote N' / 'Height N' designation "
    "is the summit elevation in METERS taken from the period French/German "
    "topographic map. Given the designation and the nearby places from the "
    "operation, locate the specific height. Prefer a summit whose elevation is "
    "close to N meters within the described area. If you cannot place it "
    "confidently, set found=false rather than guessing a far-off same-numbered "
    "hill."
)


def elevation_of(name: str) -> Optional[int]:
    """Return the meters elevation encoded in a height name, or None."""
    match = _HEIGHT_RE.search(name or "")
    return int(match.group(1)) if match else None


def is_height_feature(name: str) -> bool:
    """True if a place name is a numbered height feature."""
    return elevation_of(name) is not None


def _proximity_anchors(place: dict) -> List[str]:
    """Collect nearby-place context strings from a record's event mentions."""
    anchors: List[str] = []
    for mention in place.get("event_mentions", []) or []:
        for key in ("Sub_event_Name", "original_text", "Event_Name"):
            val = mention.get(key)
            if val and val not in anchors:
                anchors.append(str(val))
    return anchors[:3]


def _prompt_for(name: str, elevation: int, anchors: List[str]) -> str:
    """Build the elevation+context geocoding prompt."""
    context = (
        "\n".join(f"- {a}" for a in anchors) if anchors else "- (no extra context)"
    )
    return (
        f"Feature: {name!r} (summit elevation ~{elevation} meters).\n"
        f"Operational context mentioning nearby places:\n{context}\n"
        "Locate this height. Output ONLY compact JSON: "
        '{"found": true, "latitude": 49.13, "longitude": -1.00, '
        '"country": "France", "confidence": 0.7, '
        '"note": "which anchor/summit you used"}'
    )


def make_hill_geocoder(elevation_cache: Path, session: Any = None):
    """Return a ``geocode(name, place, grok_client) -> GeocodeResult`` callable.

    Uses Grok for context-aware placement, then verifies the candidate's terrain
    elevation against the designation (meters). A verified hit keeps its
    confidence; an unverifiable-but-plausible hit is downgraded and noted; a
    gross terrain mismatch is rejected (found=false) so it is not written as a
    coordinate.
    """

    def geocode(name: str, place: dict, grok_client: Any) -> GeocodeResult:
        elevation = elevation_of(name)
        if elevation is None:
            return GeocodeResult(found=False, note="not a height feature")
        result = _grok_locate(name, elevation, place, grok_client)
        if not result.found or result.latitude is None or result.longitude is None:
            return result
        return _verify(result, elevation, elevation_cache, session)

    return geocode


def _grok_locate(
    name: str, elevation: int, place: dict, grok_client: Any
) -> GeocodeResult:
    """Ask Grok to place a height using its elevation and proximity context."""
    prompt = _prompt_for(name, elevation, _proximity_anchors(place))
    try:
        raw = grok_client.chat_completion(
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.1,
            cache_type="places",
        )
    except Exception as exc:  # noqa: BLE001 - surface as a miss, keep batch alive
        logger.warning("Hill geocode (grok) failed for %s: %s", name, exc)
        return GeocodeResult(found=False, note=f"grok error: {exc}", source="grok")
    result = _parse_geocode_reply(raw)
    result.source = "grok_hill"
    return result


def _verify(
    result: GeocodeResult, elevation: int, cache: Path, session: Any
) -> GeocodeResult:
    """Verify a candidate against terrain elevation; adjust confidence/flags.

    * terrain matches (meters) -> keep, append confirmation note.
    * terrain unavailable       -> keep but cap confidence (unverified).
    * gross mismatch            -> reject (found=false) so no bad coordinate is
      written; the caller records it for review.
    """
    check = check_designation(
        elevation, result.latitude, result.longitude, cache, session
    )
    note = "; ".join(n for n in (result.note, check.note) if n)
    if check.fits_meters:
        result.note = note
        result.confidence = max(result.confidence, 0.75)
        return result
    if check.terrain_m is None:
        result.note = note
        result.confidence = min(result.confidence, 0.5)
        return result
    if check.fits_feet:
        # Rare/again-suspect: keep but flag low for human confirmation.
        result.note = note
        result.confidence = 0.4
        return result
    # Gross mismatch — almost certainly the wrong hill (e.g. wrong continent).
    return GeocodeResult(
        found=False,
        confidence=0.0,
        note=f"rejected: {check.note}",
        source="grok_hill",
    )
