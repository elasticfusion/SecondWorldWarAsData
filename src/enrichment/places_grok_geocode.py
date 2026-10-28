"""Online geocoding of un-geocoded place records via the Grok client.

The offline pass (:mod:`src.enrichment.places_geo`) leaves a work-queue of named
places that have no coordinates. This module resolves those, one place at a time,
by asking Grok for the coordinates of a *historical WWII* place (any theater —
European, Mediterranean, North African, CBI, Pacific, or naval), using the
record's hierarchy/country context to fix the region, and writing the result back
in the same nested ``coordinates`` schema the
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

import re
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
# Transient geocode failure (API outage) — NON-terminal: deliberately NOT in _already_attempted, so
# a normal (non-force) re-run RETRIES it (a one-off Nominatim/Grok outage must not permanently mark a
# real place not_found).
_STATUS_ERROR = "geocode_error"

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
    "You are a historical geographer specializing in World War II across ALL "
    "theaters: European, Mediterranean, North African, China-Burma-India (CBI), "
    "Pacific (incl. Central Pacific), and the naval theaters (North Atlantic, "
    "Pacific). Given a place name as it appears in operational records — plus any "
    "hierarchy/country context provided — return its modern coordinates. Use the "
    "provided context to place the name in the correct region (the same name can "
    "occur in different theaters). Names may be towns, rivers, forests, islands, "
    "atolls, reefs, terrain features, or open-ocean/maritime positions (which have "
    "coordinates but no country). If you cannot identify the place with reasonable "
    "confidence, say so rather than guessing."
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
    # T2-C: identity cross-walk URLs captured from the geocoder (e.g. OSM relation/way, a
    # wikidata extratag) -> folded into place.sameAs[]. Empty unless the geocoder supplied them.
    external_ids: list = Field(
        default_factory=list,
        description="Identity URLs (OSM/Wikidata) for sameAs cross-walk",
    )
    transient: bool = Field(
        default=False,
        description="True if this is a transient failure (API timeout/5xx/exception), NOT a genuine "
        "not-found — the caller must NOT persist a terminal not_found status (which would "
        "permanently skip the place on non-force re-runs).",
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
    # Skip-reason breakdown (OBSERV): why a place was skipped, so "skipped N" is never
    # a black box. Sums to `skipped`.
    skip_no_name: int = 0
    skip_non_geocodable: int = 0  # unit-relative area ("5th Division sector"), etc.
    skip_has_coords: int = 0  # already geocoded (doesn't need it)
    skip_already_attempted: int = 0  # prior attempt recorded (not forced)
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
            "skip_no_name": self.skip_no_name,
            "skip_non_geocodable": self.skip_non_geocodable,
            "skip_has_coords": self.skip_has_coords,
            "skip_already_attempted": self.skip_already_attempted,
            "details": self.details,
        }


def _norm_person_name(text: str) -> str:
    """Normalize a name for the person cross-check using the SAME normalizer the people index was
    built with (``text_utils.normalize_name``: case-fold + ASCII-fold accents + punctuation strip),
    so hyphenated/accented/apostrophe names (Saint-Lô, O'Brien) match their index keys. Fail-safe to
    a simple lowering if the import is unavailable."""
    try:
        from src.utils.text_utils import normalize_name

        return normalize_name(text or "")
    except Exception:  # noqa: BLE001
        return " ".join(re.sub(r"[^0-9a-z]+", " ", (text or "").lower()).split())


def _known_person_names(places_dir: Path) -> set:
    """Set of normalized person names from the sibling ``people/index.json`` (keys ARE normalized
    names). Used to reject a prose-fallback toponym that is actually a PERSON ("General Patton" ->
    "Patton"). Empty set if unavailable. Best-effort."""
    try:
        idx = places_dir.parent / "people" / "index.json"
        if not idx.is_file():
            return set()
        data = json.loads(idx.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return set()
        return {
            _norm_person_name(k)
            for k in data
            if not k.startswith("_")  # skip _schema_version/_last_updated
        }
    except Exception:  # noqa: BLE001 - cross-check is best-effort
        return set()


def _place_name(
    place: dict, stem: Optional[str] = None, person_names: Optional[set] = None
) -> Optional[str]:
    """Return the best display name for a place, or None.

    Primary source is an explicit name field; next the FILE STEM (the deterministic entity key is
    the already-resolved place name, e.g. ``berlin.json`` -> "berlin" / ``ulm.json`` -> "ulm"), which
    is clean by construction and must NOT be rejected for being short. Only as a last resort — a
    record carrying no name anywhere, with the toponym trapped inside a mention's prose
    ``original_text`` — do we try to recover a name from that prose, and then only a capitalized
    toponym corroborated by context (never the whole clause).
    """
    name = (
        place.get("current_name")
        or place.get("name")
        or place.get("place_name")
        or place.get("identified_as")
    )
    if name:
        return name
    # The file stem IS the place name (entity key). Clean, already-resolved — short is fine.
    if stem:
        s = stem.strip()
        if s and s.lower() not in ("index", "duplicate_report"):
            return s
    # Last resort: recover a toponym from a mention's prose.
    return _recover_name_from_mentions(place, stem, person_names)


def _recover_name_from_mentions(
    place: dict, stem: Optional[str], person_names: Optional[set]
) -> Optional[str]:
    """Last-resort name recovery: scan each mention's prose ``original_text`` for a capitalized
    toponym corroborated by the stem (never a whole clause). Rejects a candidate that matches a
    known PERSON name (a bare proper noun from prose may be a person, not a place). Returns the
    first accepted toponym or None."""
    for m in (place.get("event_mentions") or []) + (place.get("mentions") or []):
        ot = ((m or {}).get("original_text") or "").strip()
        cand = _toponym_from_prose(ot, corroborate=stem)
        if not cand:
            continue
        if person_names:
            matched = _matches_known_person(cand, person_names)
            if matched:
                logger.info(
                    "OBSERV geocode.prose_candidate_is_person rejected=%r matched_person=%r",
                    cand,
                    matched,
                )
                continue
        return cand
    return None


def _matches_known_person(candidate: str, person_names: set):
    """Return the matched person key if ``candidate`` is a known person name (optionally with a
    leading military TITLE), else None. Title-aware: after stripping a leading run of title tokens
    the remainder must EQUAL a known person key, so a place sharing a trailing surname ("Patton
    Ridge") is not rejected. KNOWN LIMITATION (accepted): a place whose LEADING words are title
    tokens and whose remainder equals a person surname ("Field Marshal Ridge" + person "ridge")
    would be rejected — rare, and the only cost is the place stays ungeocoded+flagged (not
    mis-located). The matched key is returned so a rejection is debuggable."""
    norm = _norm_person_name(candidate)
    if not norm:
        return None
    if norm in person_names:
        return norm
    tokens = norm.split()
    i = 0
    while i < len(tokens) and tokens[i] in _MILITARY_TITLE_TOKENS:
        i += 1
    if i > 0:
        remainder = " ".join(tokens[i:])
        if remainder and remainder in person_names:
            return remainder
    return None


# Leading title tokens that may prefix a person's name in prose (normalized, punctuation-stripped).
_MILITARY_TITLE_TOKENS = frozenset(
    {
        "general",
        "gen",
        "lieutenant",
        "lt",
        "colonel",
        "col",
        "major",
        "maj",
        "captain",
        "capt",
        "sergeant",
        "sgt",
        "corporal",
        "cpl",
        "private",
        "pvt",
        "field",
        "marshal",
        "admiral",
        "adm",
        "brigadier",
        "brig",
        "commander",
        "cmdr",
        "commodore",
        "president",
        "prime",
        "minister",
        "generalmajor",
        "generaloberst",
        "oberst",
        "hauptmann",
        "leutnant",
        "feldmarschall",
    }
)


# Common function/connective words that are not part of a place name. This set is deliberately
# SMALL: its only job is to bound a capitalized phrase (so "They attacked Aachen" yields "Aachen",
# not "They ... Aachen"). It intentionally does NOT try to classify proper nouns — two capitalized
# words in a row do NOT disqualify a place ("New York City", "New Jersey", "Monte Cassino"), and a
# codename/surname phrase ("Operation Cobra", "General Patton") is extracted INTACT and handed to the
# Grok geocoder, which quickly returns not-found for a non-place. Over-stripping here would fragment
# real multi-word toponyms, so we keep the list to genuine function words only.
_PROSE_CAP_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "they",
        "we",
        "he",
        "she",
        "it",
        "his",
        "her",
        "their",
        "our",
        "this",
        "that",
        "these",
        "those",
        "and",
        "but",
        "or",
        "nor",
        "for",
        "so",
        "yet",
        "on",
        "in",
        "at",
        "by",
        "to",
        "of",
        "german",
        "germans",
        "american",
        "americans",
        "british",
        "allied",
        "allies",
        "enemy",
        "when",
        "while",
        "after",
        "before",
        "during",
        "then",
        "now",
        "two",
        "three",
        "first",
        "second",
    }
)


def _toponym_from_prose(text: str, corroborate: Optional[str] = None) -> Optional[str]:
    """Recover a place name from a prose ``original_text`` using capitalization + context.

    Mid-sentence capitalization is the signal: a capitalized token that is NOT sentence-initial and
    NOT a prose stopword is very likely a proper noun. We extract the longest run of such capitalized
    tokens (handles "Monte Cassino", "Saint-Lô", "Hürtgen Forest"). To guard the hard
    sentence-initial case ("Ulm was on fire." — "Ulm" is capitalized only because it starts the
    sentence), a sentence-initial single capitalized token is accepted ONLY if ``corroborate`` (the
    file stem) matches it. Returns None when nothing qualifies (record stays ungeocoded + flagged,
    rather than geocoded off prose).
    """
    # RADON-INHERENT (C16): a single stateful scanner — the while-loop maintains run state
    # (sentence-initial tracking, run extension, boundary close) + a final corroboration guard, all
    # sharing mutable locals. Pulling the scan into a helper would pass that state bundle back and
    # forth and obscure the algorithm; the _norm/_is_cap/_ends_sentence closures are already split.
    t = (text or "").strip()
    if not t or len(t) > 200:
        return None
    # Token stream with position; a token is "name-like capitalized" if it starts with an
    # uppercase/accented-uppercase letter and is not a stopword.
    tokens = re.findall(r"[^\s]+", t)

    def _norm(tok: str) -> str:
        return re.sub(r"[^0-9A-Za-zÀ-ÿ'’-]", "", tok)

    def _is_cap(tok: str) -> bool:
        w = _norm(tok)
        if not w or w.lower() in _PROSE_CAP_STOPWORDS:
            return False
        return w[0].isupper() or (len(w) == 1 and w.isalpha() and w.isupper())

    def _ends_sentence(tok: str) -> bool:
        # A sentence terminator may be followed by closing wrappers ("Aachen." / (Metz.) / 'Caen.');
        # strip trailing wrapping chars before testing so a quoted/parenthesized boundary still
        # closes the run (otherwise adjacent-sentence cities could fuse).
        return tok.rstrip("\"'”’)]}»").endswith((".", "!", "?"))

    # Find the longest consecutive run of capitalized name-like tokens, tracking whether the run
    # begins at sentence start (index 0, or right after sentence-ending punctuation). A run STOPS at
    # a sentence boundary: a token ending in . ! ? (possibly wrapped in closing quotes/brackets)
    # closes the current run (so "Aachen. Cologne" does not fuse into a fake "Aachen Cologne" — they
    # are two separate runs, each subject to the sentence-initial corroboration guard below).
    best: list = []
    best_sentence_initial = True
    i = 0
    prev_ended_sentence = True  # position 0 is sentence-initial
    while i < len(tokens):
        if _is_cap(tokens[i]):
            run = [tokens[i]]
            run_initial = prev_ended_sentence
            # If this very token ends a sentence, the run is just this token.
            ends_sentence = _ends_sentence(tokens[i])
            j = i + 1
            while not ends_sentence and j < len(tokens) and _is_cap(tokens[j]):
                run.append(tokens[j])
                ends_sentence = _ends_sentence(tokens[j])
                j += 1
            if len(run) > len(best):
                best = run
                best_sentence_initial = run_initial
            prev_ended_sentence = _ends_sentence(tokens[j - 1])
            i = j
        else:
            prev_ended_sentence = _ends_sentence(tokens[i])
            i += 1

    if not best:
        return None
    cand = " ".join(_norm(x) for x in best).strip()
    if not cand:
        return None
    # A capitalized run that BEGINS a sentence is ambiguous — the leading token may be capitalized
    # only because it starts the sentence ("Ulm was on fire." / "Monte Cassino fell."). A
    # sentence-initial run (single OR multi-token) is therefore accepted only when the file stem
    # corroborates its first token. A run that is NOT sentence-initial is a confident mid-sentence
    # proper-noun phrase and is accepted as-is.
    if best_sentence_initial:
        if corroborate and _norm(best[0]).lower() == corroborate.strip().lower():
            return cand
        return None
    return cand


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


def _valid_wgs84(lat, lon) -> bool:
    """True if (lat, lon) are within WGS84 range. Guards against an LLM returning an
    impossible coordinate (e.g. lat=999) that must never be persisted as a geocode."""
    return (
        lat is not None
        and lon is not None
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon <= 180.0
    )


def _reject_out_of_range(place: dict, result: GeocodeResult) -> str:
    """Record a confident-but-impossible coordinate as not-found (never persist garbage)."""
    logger.warning(
        "OBSERV geocode.out_of_range place=%s lat=%s lon=%s source=%s — rejected",
        str(place.get("PlaceID") or place.get("name") or "?")[:40],
        result.latitude,
        result.longitude,
        result.source,
    )
    place["enrichment_status"] = _STATUS_NOT_FOUND
    place["geocode_source"] = result.source
    place["geo_review"] = (
        f"rejected out-of-range coordinates ({result.latitude}, {result.longitude})"
    )
    return _STATUS_NOT_FOUND


def _non_geocoded_status(place: dict, result: GeocodeResult) -> Optional[str]:
    """Handle the non-happy-path geocode outcomes (transient outage / not-found / low-confidence),
    folding the status into ``place``. Returns the status set, or None if the result is a confident
    hit that ``_apply_result`` should persist as coordinates."""
    if getattr(result, "transient", False):
        # Transient API failure — non-terminal so a non-force re-run retries (never permanently
        # mark a real place not_found on a one-off outage).
        place["enrichment_status"] = _STATUS_ERROR
        place["geocode_source"] = result.source
        logger.info(
            "OBSERV geocode.transient_error place=%s note=%s",
            str(place.get("PlaceID") or place.get("name") or "?")[:40],
            str(result.note)[:80],
        )
        return _STATUS_ERROR
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
    return None


def _apply_result(place: dict, result: GeocodeResult) -> str:
    """Fold a geocode result into ``place``; return the enrichment_status set.

    A confident hit writes nested ``coordinates`` (with precision/confidence) and
    derives bounding box / map URLs via the offline enricher. A not-found or
    low-confidence result records a status note and leaves the place un-geocoded
    (never a guessed coordinate).
    """
    early = _non_geocoded_status(place, result)
    if early is not None:
        return early

    # WGS84 range guard: a confident result with impossible coordinates (e.g. an LLM returning
    # lat=999) must NOT be persisted as a valid geocode. Reject as not-found.
    if not _valid_wgs84(result.latitude, result.longitude):
        return _reject_out_of_range(place, result)

    place["coordinates"] = {
        "latitude": result.latitude,
        "longitude": result.longitude,
        "precision": "approximate",
        "confidence": round(result.confidence, 2),
    }
    # Mirror to the top-level latitude/longitude the schema also carries at the root — the
    # semantic quality-flag check (_geo_flags) reads the TOP-LEVEL fields, so without this a
    # successfully-geocoded place would still be flagged geo_missing_coords.
    place["latitude"] = result.latitude
    place["longitude"] = result.longitude
    if result.country:
        place["country"] = result.country
    place["geocode_source"] = result.source
    place["enrichment_status"] = _STATUS_GEOCODED
    # T2-C: fold any identity cross-walk ids the geocoder captured (OSM/Wikidata) into sameAs[].
    ext = getattr(result, "external_ids", None)
    if ext:
        try:
            from src.extraction.enrich_places import _add_place_sameas

            _add_place_sameas(place, extra=list(ext))
            logger.info("OBSERV place.sameas_osm count=%d", len(ext))
        except Exception as e:  # noqa: BLE001
            logger.debug("sameAs(osm) fold skipped: %s", e)
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

    # Build the known-person-names set ONCE for the run (entity-context cross-check, #449 follow-up).
    person_names = _known_person_names(places_dir)
    if person_names:
        logger.info(
            "OBSERV geocode.person_crosscheck_loaded names=%d", len(person_names)
        )
    else:
        logger.info(
            "OBSERV geocode.person_crosscheck_unavailable (no people index) — prose "
            "toponym person-disambiguation disabled this pass"
        )

    for path in sorted(places_dir.glob("*.json")):
        if path.name in _SKIP_FILES:
            continue
        if limit is not None and report.attempted >= limit:
            break
        _geocode_one_file(
            path, grok_client, write, force, report, resolve, person_names
        )

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
    try:
        logger.info(
            "OBSERV geocode.dir attempted=%d geocoded=%d not_found=%d low_conf=%d "
            "errors=%d skipped=%d skip_reasons={no_name:%d,non_geocodable:%d,"
            "has_coords:%d,already_attempted:%d}",
            report.attempted,
            report.geocoded,
            report.not_found,
            report.low_confidence,
            report.errors,
            report.skipped,
            report.skip_no_name,
            report.skip_non_geocodable,
            report.skip_has_coords,
            report.skip_already_attempted,
        )
    except Exception:  # noqa: BLE001 - observability must never break the pass
        pass
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
        transient_seen: Optional[GeocodeResult] = None
        for geocoder in geocoders:
            result = geocoder(name, place, client)
            if result.found and result.confidence > MIN_GEOCODE_CONFIDENCE:
                return result
            if getattr(result, "transient", False) and transient_seen is None:
                transient_seen = result
            if best is None or result.confidence > best.confidence:
                best = result
        # No confident hit. If ANY geocoder failed transiently (outage/timeout/5xx), prefer that
        # transient result over a genuine miss — otherwise a one-off outage masked by another
        # geocoder's low-confidence miss would be persisted as a TERMINAL geocode_not_found and
        # never retried (_already_attempted skips it on a non-force re-run). A transient must stay
        # retriable.
        if transient_seen is not None and not (best and best.found):
            return transient_seen
        return best if best is not None else GeocodeResult(found=False)

    return geocode


def _geocode_one_file(
    path: Path,
    grok_client: Any,
    write: bool,
    force: bool,
    report: GeocodeRunReport,
    resolve: Any,
    person_names: Optional[set] = None,
) -> None:
    """Geocode a single place file, updating ``report`` (and file if ``write``)."""
    try:
        place = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(place, dict):
        return

    name = _place_name(place, stem=path.stem, person_names=person_names)
    # Attribute each skip to a reason (OBSERV) instead of a black-box "skipped".
    if not name:
        report.skipped += 1
        report.skip_no_name += 1
        return
    if not is_geocodable_name(name):
        report.skipped += 1
        report.skip_non_geocodable += 1
        return
    if not _needs_geocode(place):
        report.skipped += 1
        report.skip_has_coords += 1
        return
    if _already_attempted(place) and not force:
        report.skipped += 1
        report.skip_already_attempted += 1
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
    from src.schemas import inject_metadata
    from src.utils.file_lock import write_json_with_lock

    inject_metadata(place, entity="places")
    if not write_json_with_lock(path, place, entity="places"):
        logger.warning(
            "OBSERV geocode.write_blocked place=%s status=%s — schema guard refused the write",
            str(place.get("PlaceID") or place.get("name") or "?")[:40],
            place.get("enrichment_status"),
        )
