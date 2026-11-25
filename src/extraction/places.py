"""Place extraction from event data."""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid
from pydantic import BaseModel, ConfigDict, Field

from src.utils.entity_records import merge_mentions, mention_sub_event
from src.grok_client import GrokClient
from src.extraction.starvation_signal import (
    note_empty_fulltext_skip,
    warn_all_subevents_empty,
)
from src.utils.file_lock import write_json_with_lock
from src.utils.json_validator import _fix_invalid_ulids
from src.utils.yield_observer import count_nonempty_fulltext, observe_yield

from src.utils.prompt_loader import get_system_prompt

logger = logging.getLogger(__name__)

# Schema version this module targets — derived from the central per-entity map.
from src.schemas import entity_version as _entity_version

SCHEMA_TARGET = _entity_version("places")


# Pydantic schemas for structured outputs
class MapUrls(BaseModel):
    """Map service URLs."""

    google_maps: str = Field(description="Google Maps URL")
    openstreetmap: str = Field(description="OpenStreetMap URL")


class PlaceMention(BaseModel):
    """Individual place mention."""

    PlaceMentionID: Optional[str] = Field(
        default=None,
        description="server-minted (required on the stored record); LLM must NOT supply it",
    )
    current_name: str = Field(description="Current name of the place")
    historical_name: Optional[str] = Field(
        default=None, description="Historical name if different"
    )
    source_language: str = Field(
        default="English", description="Language of source text"
    )
    latitude: float = Field(
        description="Latitude coordinate (use geographic center for large regions)"
    )
    longitude: float = Field(
        description="Longitude coordinate (use geographic center for large regions)"
    )
    geography_type: str = Field(description="Type: city, country, region, sea, etc.")
    date_context: Optional[str] = Field(
        default=None, description="Date context if mentioned"
    )
    role_in_event: Optional[str] = Field(
        default=None,
        description="Role of place in event (e.g., 'target of attack', 'defensive position')",
    )
    original_text: str = Field(description="Exact text from document")
    map_urls: Optional[MapUrls] = Field(
        default=None, description="Modern map service URLs"
    )


class PlaceOutput(BaseModel):
    """Place extraction output."""

    Event_Name: str = Field(description="Name of the event")
    EventID: str = Field(description="26-character ULID of event")
    Sub_event_Name: str = Field(
        description="Name of the sub-event", alias="Sub-event_Name"
    )
    Sub_eventID: str = Field(
        description="26-character ULID of sub-event", alias="Sub-eventID"
    )
    Place_Mentions: list[PlaceMention] = Field(description="List of place mentions")

    model_config = ConfigDict(populate_by_name=True)


@lru_cache(maxsize=1000)
def _calculate_bounding_box(lat: float, lon: float) -> Dict[str, float]:
    """Calculate 100km bounding box around coordinates."""
    return {
        "north": round(lat + 0.9, 4),
        "south": round(lat - 0.9, 4),
        "east": round(lon + 0.9, 4),
        "west": round(lon - 0.9, 4),
    }


def _generate_map_urls(lat: float, lon: float) -> Dict[str, str]:
    """Generate map service URLs for coordinates."""
    return {
        "google_maps": f"https://www.google.com/maps?q={lat},{lon}",
        "openstreetmap": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12",
    }


def _is_valid_place_mention(mention: Dict[str, Any]) -> bool:
    """Check if place mention has required fields.

    A place with NULL coordinates is normally dropped (a settlement with no coordinate is an
    extraction miss). EXCEPTIONS that are KEPT with null coords so the Phase-3 geocoders can
    resolve them from context (they are designed to): a ``route``; a named-HEIGHT feature
    ("Hill 555" / "Côte 192" — hill_geocode locates it by elevation + proximity anchors); and a
    SITUATIONAL/RELATIVE feature ("east bank of the Loire", "Aachen Gap", a ridge/corridor/
    bridgehead/sector — resolved to an approximate centroid from its reference place). Phase 2
    must NOT fabricate coordinates for these; it emits them with null lat/long + context and
    Phase 3 geocodes them.
    """
    if mention.get("current_name") is None:
        logger.debug("Removed place mention with null current_name")
        return False

    has_coords = (
        mention.get("latitude") is not None and mention.get("longitude") is not None
    )
    if has_coords or "route" in mention:
        return True

    # Null coordinates: keep ONLY if it is a Phase-3-geocodable landmark (height/situational),
    # otherwise drop (a null-coord settlement/region is a genuine extraction miss).
    if _is_phase3_geocodable_landmark(mention):
        logger.info(
            "OBSERV places.landmark_kept name=%r geography_type=%r — null coords, "
            "deferred to Phase-3 geocoding",
            mention.get("current_name"),
            mention.get("geography_type"),
        )
        return True

    logger.debug(
        "Removed place '%s' with null coordinates", mention.get("current_name")
    )
    return False


# geography_type values (substring, case-insensitive) that denote a SITUATIONAL/RELATIVE feature
# resolvable by Phase-3 from a reference place rather than a gazetteer point.
_SITUATIONAL_GEO_TYPES = (
    "situational",
    "relative",
    "ridge",
    "gap",
    "corridor",
    "bridgehead",
    "beachhead",
    "sector",
    "bank",
    "landmark",
)


def _is_phase3_geocodable_landmark(mention: Dict[str, Any]) -> bool:
    """True if a null-coordinate mention is a named-height or situational/relative feature that
    the Phase-3 geocoders (hill_geocode / situational + Grok fallback) can locate from context.
    """
    name = mention.get("current_name") or ""
    try:
        from src.enrichment.hill_geocode import is_height_feature

        if is_height_feature(name):
            return True
    except Exception:  # noqa: BLE001 - never block extraction on an import issue
        pass
    gtype = (mention.get("geography_type") or "").lower()
    return any(t in gtype for t in _SITUATIONAL_GEO_TYPES)


def _add_geo_data(mention: Dict[str, Any], lat: float, lon: float) -> None:
    """Add bounding box and map URLs to a place mention."""
    mention["bounding_box"] = _calculate_bounding_box(lat, lon)
    if "map_urls" not in mention:
        mention["map_urls"] = _generate_map_urls(lat, lon)


def _process_place_mention(mention: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single place mention: fix nulls and add bounding boxes."""
    if mention.get("geography_type") is None:
        mention["geography_type"] = "Unknown"
        logger.debug("Fixed null geography_type to 'Unknown'")

    # Add bounding box and map URLs for regular places
    if mention.get("latitude") and mention.get("longitude"):
        _add_geo_data(mention, mention["latitude"], mention["longitude"])

    # Process route stops
    if "route" in mention and isinstance(mention["route"], list):
        for stop in mention["route"]:
            if stop.get("latitude") and stop.get("longitude"):
                _add_geo_data(stop, stop["latitude"], stop["longitude"])

    return mention


def _process_place_mentions(data: Dict[str, Any]) -> None:
    """Process and filter place mentions in-place."""
    if "Place_Mentions" in data and isinstance(data["Place_Mentions"], list):
        valid_mentions = []
        for mention in data["Place_Mentions"]:
            if isinstance(mention, dict) and _is_valid_place_mention(mention):
                valid_mentions.append(_process_place_mention(mention))
        data["Place_Mentions"] = valid_mentions


def _fix_null_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Fix null values in required fields and add bounding boxes."""
    _process_place_mentions(data)

    for key, value in data.items():
        if isinstance(value, dict):
            data[key] = _fix_null_fields(value)
        elif isinstance(value, list):
            data[key] = [
                _fix_null_fields(item) if isinstance(item, dict) else item
                for item in value
            ]
    return data


def create_place_prompt(
    sub_event: Dict[str, Any],
    event_id: str,
    event_name: str,
    empty_counter: Optional[dict] = None,
) -> str:
    """Create prompt for place extraction from a sub-event."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    sub_event_summary = sub_event.get("Sub-event_summary", "")
    fulltext = sub_event.get("Sub-event_fulltext", {})

    from src.utils.paragraph_numbers import in_paragraph_order

    text_parts = [text for _key, text in in_paragraph_order(fulltext)]
    text = "\n\n".join(text_parts)

    if not text.strip():
        note_empty_fulltext_skip("places", sub_event_id, empty_counter)
        return ""

    from src.utils.prompt_loader import get_system_prompt, render_prompt

    prompt = render_prompt(
        "places",
        event_name=event_name,
        event_id=event_id,
        sub_event_summary=sub_event_summary,
        sub_event_id=sub_event_id,
        text=text,
    )

    return prompt


def _load_places_index(places_dir: Path) -> dict:
    """Load existing places index."""
    from src.extraction._entity_common import load_json_index

    return load_json_index(places_dir / "index.json")


def _load_book_metadata(parsed_file: Optional[Path]) -> tuple[str, str, str]:
    """Load book metadata from parsed file. Returns (book, author, series)."""
    from src.extraction._entity_common import load_book_metadata

    return load_book_metadata(parsed_file)


def _extract_place_for_sub_event(
    sub_event: Dict[str, Any],
    event_id: str,
    event_name: str,
    grok_client: GrokClient,
    max_retries: int,
    empty_counter: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """Extract places for a single sub-event with retry logic."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    logger.info("  Processing sub-event %s", sub_event_id)

    prompt = create_place_prompt(sub_event, event_id, event_name, empty_counter)
    if not prompt:
        return None

    for attempt in range(max_retries):
        try:
            place_output = grok_client.extract_structured(
                prompt=prompt,
                schema=PlaceOutput,
                system_prompt=get_system_prompt("places"),
                use_cache=(attempt == 0),
                cache_type="places",
            )

            place_dict: Dict[str, Any] = place_output.model_dump(by_alias=True)
            place_dict = _fix_null_fields(place_dict)  # type: ignore
            place_dict = _fix_invalid_ulids(place_dict)  # type: ignore

            # HIGH#7b: verify Grok honored the embedded Sub-eventID echo (observability only).
            from src.utils.deterministic_ids import observe_embedded_ulid

            observe_embedded_ulid(sub_event_id, place_dict.get("Sub-eventID"), "places")

            return place_dict

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning("    Attempt %d failed: %s. Retrying...", attempt + 1, e)
            else:
                logger.error(
                    "    All %d attempts failed for %s", max_retries, sub_event_id
                )
                raise

    return None


def extract_places(
    event_file: Path,
    grok_client: GrokClient,
    places_dir: Path,
    parsed_file: Optional[Path] = None,
    max_retries: int = 3,
) -> Optional[Path]:
    """
    Extract places from event file and add to central repository.

    Args:
        event_file: Path to event JSON file
        grok_client: Grok API client
        places_dir: Central places directory (output/places/)
        parsed_file: Path to parsed JSON file (for book metadata)
        max_retries: Maximum retry attempts per sub-event

    Returns:
        Path to places directory, or None if failed
    """
    places_dir.mkdir(parents=True, exist_ok=True)
    index = _load_places_index(places_dir)

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    # Get book metadata
    book, author, series = _load_book_metadata(parsed_file)

    # Validate required metadata (series is optional)
    from src.extraction._entity_common import require_book_provenance

    require_book_provenance(book, author, "places", str(parsed_file))

    event_name = event_data.get("Chapter", "")
    event_obj = event_data.get("Event", {})
    event_id = event_obj.get("EventID", "")
    sub_events = event_obj.get("Sub-events", [])

    places_updated = 0
    empty_counter: dict = {"empty": 0}

    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_name = sub_event.get("Sub-event_summary", "")

        place_dict = _extract_place_for_sub_event(
            sub_event, event_id, event_name, grok_client, max_retries, empty_counter
        )

        if not place_dict:
            continue

        # Process each place mention
        for mention in place_dict.get("Place_Mentions", []):
            place_name = mention.get("current_name")
            if not place_name:
                continue

            # Find or create place file; a record the schema guard refuses skips this mention
            # only (task 1.8), loudly - the other places of the event are still written
            try:
                place_file = _find_or_create_place(
                    place_name, mention, places_dir, index
                )
            except RuntimeError as e:
                from src.utils.observe import observe_warn

                observe_warn(
                    "places",
                    "record_refused",
                    note=(
                        "a new place record failed its schema and was not written; check the "
                        "places schema against the extractor's fields (the task log has the "
                        "validation error)"
                    ),
                    place=str(place_name)[:80],
                    error=str(e)[:120],
                )
                continue

            # Add event mention to place file
            _add_event_mention(
                place_file,
                mention,
                event_name,
                event_id,
                sub_event_name,
                sub_event_id,
                book,
                author,
                series,
            )

            places_updated += 1

        logger.info(
            "  ✓ Processed %d place mentions",
            len(place_dict.get("Place_Mentions", [])),
        )

    # Merge our in-memory index entries into the AUTHORITATIVE on-disk index under the
    # lock (never a full overwrite — that would clobber a concurrent/other-phase writer's
    # entries). _find_or_create_place already persisted each new entry under the same lock;
    # this union is a belt-and-suspenders for any entry only in this snapshot.
    _persist_places_index(places_dir, index)

    _observe_places_yield(
        event_data, event_id, sub_events, places_updated, empty_counter
    )
    return places_dir if places_updated > 0 else None


def _observe_places_yield(
    event_data, event_id, sub_events, places_updated, empty_counter
):
    """Emit the places extract rollup + expected-yield baseline + all-empty alarm. Fail-safe."""
    logger.info("  Updated %d place(s) in central repository", places_updated)
    logger.info(
        "OBSERV places.extract event_id=%s sub_events=%d places_updated=%d",
        event_id or "?",
        len(sub_events),
        places_updated,
    )
    observe_yield(
        "places",
        event_id or "?",
        len(sub_events),
        count_nonempty_fulltext(event_data),
        places_updated,
    )
    warn_all_subevents_empty(
        "places", event_id or "?", len(sub_events), int(empty_counter.get("empty", 0))
    )


def _persist_places_index(places_dir: Path, index: Dict[str, str]) -> None:
    """Union the in-memory index entries into the authoritative on-disk index under the
    lock — never a full overwrite (which would clobber a concurrent writer's entries).
    """
    from src.utils.file_lock import locked_json

    with locked_json(places_dir / "index.json") as (disk_index, save):
        changed = False
        for k, v in index.items():
            if disk_index.get(k) != v:
                disk_index[k] = v
                changed = True
        if changed:
            save(disk_index)


def _new_place_filename(place_name: str, place_id: str, places_dir: Path) -> str:
    """Containment-safe filename for a new place (symlink-resolved; ULID fallback)."""
    from src.extraction.batch_parallel import (
        _safe_entity_filename,
        _contained_entity_path,
        entity_id_suffix,
    )

    safe_name = place_name.replace(" ", "_").replace("/", "_")[:120]
    stem = _safe_entity_filename(safe_name, "place")[:-5]
    filename = _safe_entity_filename(
        f"{stem}_{entity_id_suffix(place_id, places_dir, stem)}.json",
        f"place_{place_id[-12:]}",
    )
    if _contained_entity_path(places_dir, filename) is None:
        filename = f"place_{place_id}.json"
    return filename


def _build_new_place_record(place_name: str, mention: Dict[str, Any]) -> Dict[str, Any]:
    """Build a new place record (id minted server-side + coordinates + Phase-2 enrich).

    Side-effect-free w.r.t. disk/index; the caller persists under the index lock."""
    # HIGH#7a: PlaceID is a NEW entity id — MINT it server-side. Previously this reused the LLM's
    # PlaceMentionID (both trusting an LLM id AND conflating a per-mention id with the entity id).
    llm_place_id = mention.get("PlaceMentionID")
    place_id = str(ulid.new())
    if llm_place_id and llm_place_id != place_id:
        logger.info("OBSERV id.minted entity=place field=PlaceID replaced_llm_value=1")

    place_data: Dict[str, Any] = {
        "PlaceID": place_id,
        "current_name": place_name,
        "historical_names": [],
        "aliases": [],
        "source_language": mention.get("source_language", "English"),
        "geography_type": mention.get("geography_type", "other"),
        "event_mentions": [],
    }

    if mention.get("latitude") and mention.get("longitude"):
        lat = mention["latitude"]
        lon = mention["longitude"]
        place_data["coordinates"] = {
            "latitude": lat,
            "longitude": lon,
            "precision": "approximate",
            "confidence": 0.8,
        }
        place_data["bounding_box"] = mention.get("bounding_box")
        place_data["map_urls"] = mention.get("map_urls") or _generate_map_urls(lat, lon)

    # Phase-2 Grokipedia + Wikipedia descriptive lookup. Phase 2 owns ALL Grokipedia/Wikipedia
    # enrichment; this augments in place before the native save. Fail-safe: never block extraction.
    try:
        from src.extraction.enrich_places import (
            enrich_place_from_grokipedia,
            enrich_place_from_wikipedia,
        )

        enrich_place_from_grokipedia(place_data)
        enrich_place_from_wikipedia(place_data)
    except Exception as e:  # noqa: BLE001 - enrichment is best-effort
        logger.warning("  Grokipedia enrichment failed for place %s: %s", place_name, e)

    return place_data


def _find_or_create_place(
    place_name: str,
    mention: Dict[str, Any],
    places_dir: Path,
    index: Dict[str, str],
) -> Path:
    """Find existing place file or create a new one — conflict-safe WITHOUT a queue.

    The find-vs-create decision is serialised on the shared ``index.json`` under the
    cross-process ``locked_json`` lock: the authoritative index is re-read INSIDE the
    lock (so a concurrent/other-phase writer's just-created entry is seen), and a new
    entry is persisted to ``index.json`` under the same lock before release. Two
    overlapping writers (e.g. Phase 2 and a Phase 3 feedback re-run) therefore
    converge to ONE file instead of each minting a duplicate that last-write-wins
    would orphan. The passed-in in-memory ``index`` is still updated for the caller's
    subsequent lookups, but the on-disk locked index is the source of truth."""
    from src.utils.file_lock import locked_json

    place_key = place_name.lower()

    # Fast path: already known to this in-memory snapshot.
    if place_key in index:
        return places_dir / index[place_key]

    index_file = places_dir / "index.json"
    with locked_json(index_file) as (disk_index, save):
        # Re-check under the lock against the AUTHORITATIVE on-disk index — a concurrent
        # writer may have created this place since our snapshot was taken.
        existing = disk_index.get(place_key)
        if existing and not str(place_key).startswith("_"):
            index[place_key] = existing  # refresh the caller's snapshot
            return places_dir / existing

        # Still absent under the lock -> we are the creator. Build + persist the record,
        # then register it in the locked index before releasing (so the next waiter sees it).
        place_data = _build_new_place_record(place_name, mention)
        filename = _new_place_filename(place_name, place_data["PlaceID"], places_dir)
        if not write_json_with_lock(places_dir / filename, place_data):
            # the schema guard refused the record: never index a file that does not exist
            raise RuntimeError(
                f"place record for {place_name!r} was refused by its schema"
            )

        disk_index[place_key] = filename
        save(disk_index)
        index[place_key] = filename
        logger.debug("  Created new place: %s", filename)
        return places_dir / filename


def _build_place_name_index(
    places_dir: Path,
) -> tuple[Dict[str, str], Dict[Path, Dict[str, Any]]]:
    """Build lowercase name → PlaceID index and load all place data."""
    skip = {"index.json", "not_duplicates.json"}
    name_to_id: Dict[str, str] = {}
    file_data: Dict[Path, Dict[str, Any]] = {}
    for pf in places_dir.glob("*.json"):
        if pf.name in skip:
            continue
        try:
            with open(pf, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or not data.get("PlaceID"):
                continue
            file_data[pf] = data
            for name in _record_names(data):
                name_to_id[name] = data["PlaceID"]
        except (OSError, json.JSONDecodeError):
            continue
    return name_to_id, file_data


def _record_names(data: Dict[str, Any]) -> List[str]:
    """A place's lowercase name and aliases (non-text aliases ignored)."""
    names = [data.get("current_name")] + list(data.get("aliases") or [])
    return [n.lower() for n in names if isinstance(n, str) and n]


# How coarse a place is. A parent must be coarser than its child and of the type its hierarchy
# field names (owner 2026-11-17: village/town/city < region < country < continent). Anything not
# listed (towns, rivers, bridges, ports, "other", unknown) is a local feature (rank 1).
_RANK = {"region": 2, "state": 2, "province": 2, "country": 3, "continent": 4}
# the hierarchy field -> the types its parent may be
_FIELD_TYPES = {
    "region": ("region", "state", "province"),
    "country": ("country",),
    "continent": ("continent",),
}


def _rank(data: Dict[str, Any]) -> int:
    return _RANK.get(str(data.get("geography_type") or "").lower(), 1)


def _name_candidates(
    file_data: Dict[Path, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """lowercase name or alias -> EVERY place with it (never last-one-wins)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for data in file_data.values():
        names = [data.get("current_name") or ""] + list(data.get("aliases") or [])
        for name in {str(n).strip().lower() for n in names if n}:
            found = out.setdefault(name, [])
            if not any(d is data for d in found):
                found.append(data)
    return out


def _fits(
    found: List[Dict[str, Any]], field: str, child: Dict[str, Any]
) -> "tuple[List[Dict[str, Any]], bool]":
    """The candidates that may be ``child``'s parent for ``field``, and whether they are of a
    different type than the field names. A candidate must be a container (region, state,
    country, continent) coarser than the child; those of the field's own type win. A hierarchy
    whose field is mislabelled (``country: Devon`` for the region Devon) still links to the
    place it names, and is counted (``field_mismatch``) so the data can be corrected."""
    own = child.get("PlaceID")
    coarser = [
        c
        for c in found
        if str(c.get("geography_type") or "").lower() in _RANK
        and _rank(c) > _rank(child)
        and c.get("PlaceID") != own
    ]
    exact = [
        c
        for c in coarser
        if str(c.get("geography_type") or "").lower() in _FIELD_TYPES[field]
    ]
    return (exact, False) if exact else (coarser, bool(coarser))


def _find_parent_id(
    data: Dict[str, Any],
    candidates: Dict[str, List[Dict[str, Any]]],
    parent_of: Dict[str, str],
) -> "tuple[Optional[str], str, Optional[str]]":
    """(parent PlaceID, outcome, the name that was ambiguous). Tries the hierarchy's region, then
    country, then continent: a candidate counts only if it is a container coarser than the place
    (of the field's own type first, ``_fits``); exactly one must remain (several = ``ambiguous``: never guessed); a link that
    would close a cycle is ``cycle``. Outcomes: linked | linked_field_mismatch | ambiguous |
    cycle | not_found.
    """
    hierarchy = data.get("hierarchy") or {}
    for field in ("region", "country", "continent"):
        name = str(hierarchy.get(field) or "").strip().lower()
        fits, mismatch = (
            _fits(candidates.get(name, []), field, data) if name else ([], False)
        )
        if len(fits) > 1:
            return None, "ambiguous", name
        if fits:
            parent = str(fits[0]["PlaceID"])
            if _closes_cycle(str(data.get("PlaceID") or ""), parent, parent_of):
                return None, "cycle", None
            return parent, ("linked_field_mismatch" if mismatch else "linked"), name
    return None, "not_found", None


def _closes_cycle(own: str, parent: str, parent_of: Dict[str, str]) -> bool:
    """Whether linking ``own`` -> ``parent`` makes a loop (``own`` is above ``parent``)."""
    seen, node = set(), parent
    while node and node not in seen:
        if node == own:
            return True
        seen.add(node)
        node = parent_of.get(node, "")
    return False


def _current_parents(file_data: Dict[Path, Dict[str, Any]]) -> Dict[str, str]:
    """PlaceID -> its parent's PlaceID, for the links that exist (for the cycle check)."""
    out: Dict[str, str] = {}
    for d in file_data.values():
        h = d.get("hierarchy")
        if isinstance(h, dict) and h.get("parent_place_id"):
            out[str(d.get("PlaceID"))] = str(h["parent_place_id"])
    return out


def _log_name(value: Any) -> str:
    """A place name safe for a log line (untrusted text: control characters dropped, capped)."""
    from src.utils.find_queue import clean_for_log

    return clean_for_log(str(value or ""), 80)


def _needs_parent(hierarchy: Any) -> bool:
    return (
        isinstance(hierarchy, dict)
        and bool(hierarchy)
        and not hierarchy.get("parent_place_id")
    )


def link_parent_place_ids(places_dir: Path) -> int:
    """Link ``hierarchy.parent_place_id`` for places that have a hierarchy and no parent yet.

    The parent is the place named by the hierarchy's region, else country, else continent - only
    a place of that type and coarser than the child (``_find_parent_id``). A name shared by
    several such places is not guessed (counted and said, with examples), nor is a link that
    would close a cycle. Returns the number of places linked.
    """
    if not places_dir.exists():
        return 0
    _names, file_data = _build_place_name_index(places_dir)
    candidates = _name_candidates(file_data)
    parent_of = _current_parents(file_data)
    counts = {"linked": 0, "field_mismatch": 0, "ambiguous": 0, "cycle": 0,
              "not_found": 0, "refused": 0}  # fmt: skip
    ambiguous: Dict[str, int] = {}
    noted: Dict[str, List[str]] = {
        "field_mismatch": [],
        "cycle": [],
    }  # examples to name
    for pf, data in sorted(file_data.items(), key=lambda kv: -_rank(kv[1])):
        if not _needs_parent(data.get("hierarchy")):
            continue
        parent_id, outcome, name = _find_parent_id(data, candidates, parent_of)
        if outcome == "linked_field_mismatch":
            counts["field_mismatch"] += 1  # linked below, and said
            noted["field_mismatch"].append(
                f"{_log_name(data.get('current_name'))} -> {_log_name(name)}"
            )
        elif outcome == "cycle":
            noted["cycle"].append(_log_name(data.get("current_name") or pf.name))
        if outcome.startswith("linked"):
            outcome = _write_parent(pf, data, str(parent_id), parent_of)
        counts[outcome] += 1
        if outcome == "ambiguous" and name:
            ambiguous[_log_name(name)] = ambiguous.get(_log_name(name), 0) + 1
    _say_parent_links(counts, ambiguous, noted)
    return counts["linked"]


def _write_parent(
    pf: Path, data: Dict[str, Any], parent_id: str, parent_of: Dict[str, str]
) -> str:
    """Write the link; ``linked`` or ``refused`` (the schema guard said no)."""
    data["hierarchy"]["parent_place_id"] = parent_id
    # entity="places": the version and _last_updated stamp, so change detection sees the new
    # parent (task 1.10); a refused write is not counted as linked
    if write_json_with_lock(pf, data, entity="places"):
        parent_of[str(data["PlaceID"])] = parent_id
        return "linked"
    logger.warning(
        "OBSERV places.parent_link_refused place=%s status=warn - the schema guard refused the "
        "record; check its fields against the places schema",
        pf.name,
    )
    return "refused"


def _say_parent_links(
    counts: Dict[str, int], ambiguous: Dict[str, int], noted: Dict[str, List[str]]
) -> None:
    bad = counts["ambiguous"] or counts["cycle"] or counts["refused"]
    logger.info(
        "OBSERV places.parent_link linked=%d field_mismatch=%d ambiguous=%d cycle=%d "
        "not_found=%d refused=%d status=%s",
        counts["linked"],
        counts["field_mismatch"],
        counts["ambiguous"],
        counts["cycle"],
        counts["not_found"],
        counts["refused"],
        "warn" if bad else "ok",
    )
    if counts["field_mismatch"]:
        logger.info(
            "OBSERV places.parent_link_field_mismatch n=%d examples=%s - a hierarchy names its "
            "parent under the wrong field (country: Devon for the region Devon); linked to the "
            "coarser place it names. Check these: a field naming a country that matched a "
            "same-named region is wrong (fix the place's hierarchy); the text comes from Grok's "
            "place enrichment (enrich_places)",
            counts["field_mismatch"],
            "; ".join(noted["field_mismatch"][:10]),
        )
    if counts["ambiguous"] or counts["cycle"]:
        examples = sorted(ambiguous, key=lambda n: -ambiguous[n])[:10]
        logger.warning(
            "OBSERV places.parent_link_unresolved ambiguous=%d cycle=%d names=%s "
            "cycle_places=%s status=warn - "
            "these places were not given a parent: several places of the right type share the "
            "parent's name (duplicates to merge, or names to disambiguate), or the link would "
            "make a loop (check the places' hierarchy fields)",
            counts["ambiguous"],
            counts["cycle"],
            "; ".join(examples) or "-",
            "; ".join(noted["cycle"][:10]) or "-",
        )


def _backfill_place_data(place_data: Dict[str, Any], mention: Dict[str, Any]) -> None:
    """Backfill missing v3 schema fields on legacy place files."""
    # Rename 'name' → 'current_name' if needed
    if "name" in place_data and "current_name" not in place_data:
        place_data["current_name"] = place_data.pop("name")

    place_data.setdefault("historical_names", [])
    place_data.setdefault("aliases", [])
    place_data.setdefault("source_language", mention.get("source_language", "English"))

    if not place_data.get("geography_type"):
        place_data["geography_type"] = mention.get("geography_type", "other")

    # Add coordinates if missing
    lat = mention.get("latitude", 0)
    lon = mention.get("longitude", 0)
    if lat and lon and "coordinates" not in place_data:
        place_data["coordinates"] = {
            "latitude": lat,
            "longitude": lon,
            "precision": "approximate",
            "confidence": 0.8,
        }
        place_data["bounding_box"] = _calculate_bounding_box(lat, lon)
        place_data["map_urls"] = _generate_map_urls(lat, lon)

    # Add historical name if provided and not already present
    hist_name = mention.get("historical_name")
    if hist_name:
        existing = {h["name"] for h in place_data.get("historical_names", [])}
        if hist_name not in existing:
            place_data["historical_names"].append(
                {
                    "name": hist_name,
                    "language": mention.get("source_language", "English"),
                }
            )


def _add_event_mention(
    place_file: Path,
    mention: Dict[str, Any],
    event_name: str,
    event_id: str,
    sub_event_name: str,
    sub_event_id: str,
    book: str,
    author: str,
    series: str,
) -> None:
    """Add event mention to place file."""
    from src.utils.file_lock import locked_json
    from src.schemas.schema_contract import read_for_update

    with locked_json(place_file) as (place_data, save):
        # Read the historical record under this module's schema contract: a FUTURE-schema
        # record is skipped gracefully (code not built for it); older records merge
        # best-effort (upgrade case-by-case if registered).
        place_data, skip = read_for_update(
            place_data, SCHEMA_TARGET, logger, place_file.name
        )
        if not skip:
            # Backfill missing v3 fields from mention data
            _backfill_place_data(place_data, mention)

            # Create event mention
            event_mention = {
                "MentionID": str(ulid.new()),
                "Event_Name": event_name,
                "EventID": event_id,
                "Sub_event_Name": sub_event_name,
                "Sub_eventID": sub_event_id,
                "book": book,
                "author": author,
                "series": series,
                "date_context": mention.get("date_context"),
                "DateMentionID": None,  # TODO: Link to dates extraction
                "role_in_event": mention.get("role_in_event"),
                "original_text": mention.get("original_text", ""),
            }

            # The mention may already be there (entity_records.merge_mentions: the same statement
            # of the same sub-event). A legacy mention predating the sub-event field is noted.
            for m in place_data.get("event_mentions", []):
                if isinstance(m, dict) and not mention_sub_event(m):
                    logger.info(
                        "OBSERV places.legacy_mention_no_subevent file=%s",
                        place_file.name,
                    )
            if merge_mentions(
                place_data.setdefault("event_mentions", []), [event_mention]
            ):
                save(place_data)
                logger.debug(f"  Added mention to {place_file.name}")
