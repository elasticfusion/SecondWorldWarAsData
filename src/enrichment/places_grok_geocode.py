"""Online geocoding of un-geocoded place records via the Grok client.

The offline pass (:mod:`src.enrichment.places_geo`) leaves a work-queue of named
places that have no coordinates. This module resolves those, one place at a time,
by asking Grok for the coordinates of a *historical WWII European Theater* place
and writing the result back in the same nested ``coordinates`` schema the
extractor uses, then deriving ``bounding_box``/``map_urls`` via the offline
enricher (single source of truth for that derivation).

Design choices that match the existing pipeline:

* Uses :meth:`GrokClient.extract_structured` with ``cache_type="places"`` so
  repeated runs hit the shared places cache (no duplicate spend).
* Verification, not fabrication: Grok returns a confidence and may return
  ``found=false``; a low-confidence or not-found result is recorded as an
  ``enrichment_status`` note and left un-geocoded (never a guessed coordinate).
* Idempotent: a place already geocoded, or already marked attempted, is skipped
  unless ``force=True``.

Only places whose name looks like a real settlement/feature are attempted
(unit-relative areas such as "5th Division sector" are skipped — see
:func:`src.enrichment.places_geo.is_geocodable_name`).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from src.enrichment.places_geo import enrich_place, is_geocodable_name

logger = logging.getLogger(__name__)

# Confidence at/below which a Grok geocode is treated as unreliable: recorded for
# review but not written as a coordinate.
MIN_GEOCODE_CONFIDENCE = 0.5

# Marks a place's enrichment_status once a geocode has been attempted, so a
# re-run does not re-spend on it. Source-neutral (the geocoder may be OSM, Grok,
# or a cascade); the specific geocoder is recorded in the place's
# ``geocode_source`` field for provenance.
_STATUS_GEOCODED = "geocoded"
_STATUS_NOT_FOUND = "geocode_not_found"
_STATUS_LOW_CONF = "geocode_low_confidence"

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

_SYSTEM_PROMPT = (
    "You are a historical geographer specializing in the World War II European "
    "Theater of Operations (Western Front, 1944-1945). Given a place name as it "
    "appears in US Army operational records, return its modern coordinates. Many "
    "names are small French, Belgian, Dutch, Luxembourgish, or German towns, "
    "rivers, forests, or terrain features. If you cannot identify the place with "
    "reasonable confidence, say so rather than guessing."
)


class GeocodeResult(BaseModel):
    """Structured geocode answer from Grok."""

    found: bool = Field(description="True if the place was confidently identified")
    latitude: Optional[float] = Field(default=None, description="WGS84 latitude")
    longitude: Optional[float] = Field(default=None, description="WGS84 longitude")
    country: Optional[str] = Field(
        default=None, description="Modern country name, if known"
    )
    confidence: float = Field(
        default=0.0, description="Confidence in [0,1] that the coordinates are correct"
    )
    note: Optional[str] = Field(
        default=None, description="Any disambiguation or uncertainty note"
    )
    source: str = Field(
        default="grok",
        description="Geocoder that produced this result (e.g. grok, osm)",
    )


@dataclass
class GeocodeRunReport:
    """Summary of a Grok geocoding run."""

    attempted: int = 0
    geocoded: int = 0
    not_found: int = 0
    low_confidence: int = 0
    skipped: int = 0
    errors: int = 0
    details: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict."""
        return {
            "attempted": self.attempted,
            "geocoded": self.geocoded,
            "not_found": self.not_found,
            "low_confidence": self.low_confidence,
            "skipped": self.skipped,
            "errors": self.errors,
            "details": self.details,
        }


def _place_name(place: dict) -> Optional[str]:
    """Return the best display name for a place, or None."""
    return (
        place.get("current_name")
        or place.get("place_name")
        or place.get("identified_as")
    )


def _needs_geocode(place: dict) -> bool:
    """True if a place has no coordinates yet."""
    coords = place.get("coordinates") or {}
    lat, lon = coords.get("latitude"), coords.get("longitude")
    return lat in (None, 0, 0.0) and lon in (None, 0, 0.0)


def _prompt_for(name: str, place: dict) -> str:
    """Build the geocoding prompt, including any hierarchy/alias context."""
    context_bits = []
    hierarchy = place.get("hierarchy")
    if hierarchy:
        context_bits.append(f"Known hierarchy/context: {hierarchy}")
    aliases = place.get("aliases") or place.get("historical_names")
    if aliases:
        context_bits.append(f"Also known as: {', '.join(map(str, aliases))}")
    context = ("\n" + "\n".join(context_bits)) if context_bits else ""
    return (
        f"Place name from WWII US Army records: {name!r}.{context}\n"
        "Return the modern WGS84 latitude/longitude, modern country, a confidence "
        "in [0,1], and set found=false if you cannot identify it confidently.\n"
        "Output ONLY a compact JSON object, no prose, of the form: "
        '{"found": true, "latitude": 50.77, "longitude": 6.08, '
        '"country": "Germany", "confidence": 0.9, "note": null}'
    )


def geocode_place(name: str, place: dict, grok_client: Any) -> GeocodeResult:
    """Ask Grok to geocode one place name; returns the structured result.

    Uses ``chat_completion`` with an explicit bare-JSON instruction and parses
    the reply into :class:`GeocodeResult`. (This is more robust than the generic
    structured-output path for terse geocode replies: it avoids a reasoning
    model wrapping the answer in prose that trips JSON parsing.)
    """
    raw = grok_client.chat_completion(
        prompt=_prompt_for(name, place),
        system_prompt=_SYSTEM_PROMPT,
        temperature=0.1,
        cache_type="places",
    )
    return _parse_geocode_reply(raw)


def _parse_geocode_reply(raw: str) -> GeocodeResult:
    """Parse a Grok reply into a GeocodeResult, tolerating code fences/prose.

    Extracts the first JSON object in the reply; a reply with no parseable JSON
    is treated as a not-found result rather than raising, so one odd reply never
    aborts a batch.
    """
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return GeocodeResult(found=False, confidence=0.0, note="no JSON in reply")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return GeocodeResult(found=False, confidence=0.0, note="unparseable reply")
    return GeocodeResult.model_validate(data)


def _apply_result(place: dict, result: GeocodeResult) -> str:
    """Fold a geocode result into ``place``; return the enrichment_status set.

    A confident hit writes nested ``coordinates`` (with precision/confidence) and
    derives bounding box / map URLs via the offline enricher. A not-found or
    low-confidence result records a status note and leaves the place un-geocoded
    (never a guessed coordinate).
    """
    if not result.found or result.latitude is None or result.longitude is None:
        place["enrichment_status"] = _STATUS_NOT_FOUND
        place["geocode_source"] = result.source
        return _STATUS_NOT_FOUND
    if result.confidence <= MIN_GEOCODE_CONFIDENCE:
        place["enrichment_status"] = _STATUS_LOW_CONF
        place["geocode_source"] = result.source
        if result.note:
            place["geo_review"] = f"low-confidence geocode: {result.note}"
        return _STATUS_LOW_CONF

    place["coordinates"] = {
        "latitude": result.latitude,
        "longitude": result.longitude,
        "precision": "approximate",
        "confidence": round(result.confidence, 2),
    }
    if result.country:
        place["country"] = result.country
    place["geocode_source"] = result.source
    place["enrichment_status"] = _STATUS_GEOCODED
    enrich_place(place)  # derive bounding_box + map_urls + any outlier flag
    return _STATUS_GEOCODED


def _already_attempted(place: dict) -> bool:
    """True if this place was already geocoded or attempted.

    Recognizes the current source-neutral statuses and the legacy ``grok_*``
    names an earlier run wrote, so a normal (non-force) re-run does not
    re-attempt them.
    """
    return place.get("enrichment_status") in (
        _STATUS_GEOCODED,
        _STATUS_NOT_FOUND,
        _STATUS_LOW_CONF,
        # Legacy names from before status labels were made source-neutral.
        "grok_geocoded",
        "grok_geocode_not_found",
        "grok_geocode_low_confidence",
    )


def geocode_places_dir(
    places_dir: Path,
    grok_client: Any,
    write: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    geocoder: Optional[Any] = None,
) -> GeocodeRunReport:
    """Geocode un-geocoded, geocodable place files in ``places_dir`` via Grok.

    Args:
        places_dir: The places output directory.
        grok_client: A ``GrokClient`` (or compatible) exposing
            ``chat_completion``. May be None if a non-LLM ``geocoder`` is given.
        write: Persist updated place files when True.
        force: Re-attempt places already marked attempted.
        limit: Optional cap on the number of geocode calls (for a bounded run).
        geocoder: Optional ``geocode(name, place, client) -> GeocodeResult``
            callable used instead of the default Grok geocoder. Use
            :func:`cascade_geocoder` to try one geocoder then fall back to
            another (e.g. Nominatim first, Grok for misses).

    Returns:
        A :class:`GeocodeRunReport`.
    """
    resolve = geocoder or geocode_place
    report = GeocodeRunReport()
    if not places_dir.is_dir():
        return report

    for path in sorted(places_dir.glob("*.json")):
        if path.name in _SKIP_FILES:
            continue
        if limit is not None and report.attempted >= limit:
            break
        _geocode_one_file(path, grok_client, write, force, report, resolve)

    logger.info(
        "Geocoding: attempted %d, geocoded %d, not-found %d, low-conf %d, "
        "skipped %d, errors %d",
        report.attempted,
        report.geocoded,
        report.not_found,
        report.low_confidence,
        report.skipped,
        report.errors,
    )
    return report


def cascade_geocoder(*geocoders: Any) -> Any:
    """Compose geocoders into one that tries each until a confident hit.

    Returns a ``geocode(name, place, client) -> GeocodeResult`` callable that
    calls each geocoder in order and returns the first result that is ``found``
    with confidence above the low threshold. If none is confident, it returns
    the best (highest-confidence) result seen — so a low-confidence match is
    still surfaced (and flagged) rather than discarded.
    """

    def geocode(name: str, place: dict, client: Any) -> GeocodeResult:
        best: Optional[GeocodeResult] = None
        for geocoder in geocoders:
            result = geocoder(name, place, client)
            if result.found and result.confidence > MIN_GEOCODE_CONFIDENCE:
                return result
            if best is None or result.confidence > best.confidence:
                best = result
        return best if best is not None else GeocodeResult(found=False)

    return geocode


def _geocode_one_file(
    path: Path,
    grok_client: Any,
    write: bool,
    force: bool,
    report: GeocodeRunReport,
    resolve: Any,
) -> None:
    """Geocode a single place file, updating ``report`` (and file if ``write``)."""
    try:
        place = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(place, dict):
        return

    name = _place_name(place)
    if (
        not name
        or not is_geocodable_name(name)
        or not _needs_geocode(place)
        or (_already_attempted(place) and not force)
    ):
        report.skipped += 1
        return

    report.attempted += 1
    try:
        result = resolve(name, place, grok_client)
    except Exception as exc:  # noqa: BLE001 - record and continue the batch
        report.errors += 1
        report.details.append({"name": name, "error": str(exc)})
        logger.warning("Geocode failed for %s: %s", name, exc)
        return

    status = _apply_result(place, result)
    _tally(report, status)
    report.details.append(
        {"name": name, "status": status, "confidence": result.confidence}
    )
    if write:
        _write_place(path, place)


def _tally(report: GeocodeRunReport, status: str) -> None:
    """Increment the report counter matching a geocode status."""
    if status == _STATUS_GEOCODED:
        report.geocoded += 1
    elif status == _STATUS_NOT_FOUND:
        report.not_found += 1
    elif status == _STATUS_LOW_CONF:
        report.low_confidence += 1


def _write_place(path: Path, place: dict) -> None:
    """Persist an updated place record via the durable writer."""
    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(path, place)
