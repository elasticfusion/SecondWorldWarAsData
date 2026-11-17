"""Shared Map-record builders — the single home for the fields common to EVERY map source.

Step 2 of the maps refactor (consolidation): the source-document (`maps.py`), curated-external
(`external_maps.py`) and web-search (`openserp_maps.py`) builders each hand-wrote the SAME ~18
common Map fields (MapID, title, Event/Place/Date links, local_path, extracted_date, map_type,
storage_backend, ...). That is genuine copy-paste. This module owns the common skeleton; each
source-specific builder calls it and MERGES only its own fields (source_book/figure for the
source-document map; license/creator/external_source for the external/web maps). No behavior
change — the emitted keys/values are identical to the former inline dicts.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid

logger = logging.getLogger(__name__)

from src.utils.observe import observe_warn  # noqa: E402


def base_map_record(
    *,
    map_id: str,
    map_title: Optional[str],
    description: Optional[str],
    map_type: Optional[str],
    source_url: Optional[str],
    local_path: Optional[str],
    local_image_path: Optional[str],
    file_format: Optional[str],
    storage_backend: str,
    event_id: Optional[str] = None,
    event_name: Optional[str] = None,
    sub_event_id: Optional[str] = None,
    sub_event_name: Optional[str] = None,
    place_name: Optional[str] = None,
    place_mention_id: Optional[str] = None,
    date: Optional[str] = None,
    date_mention_id: Optional[str] = None,
    people_group_id: Optional[str] = None,
    people_group_mention_id: Optional[str] = None,
    people_group_name: Optional[str] = None,
) -> Dict[str, Any]:
    """The fields common to every Map record, regardless of source. Source-specific builders merge
    their own keys onto this (``{**base_map_record(...), "license": ...}``)."""
    return {
        "MapID": map_id,
        "map_title": map_title,
        "description": description,
        "map_type": map_type,
        "source_url": source_url,
        "local_path": local_path,
        "local_image_path": local_image_path,
        "file_format": file_format,
        "storage_backend": storage_backend,
        "EventID": event_id,
        "Event_Name": event_name,
        "Sub_eventID": sub_event_id,
        "Sub_event_Name": sub_event_name,
        "place_name": place_name,
        "PlaceMentionID": place_mention_id,
        "date": date,
        "DateMentionID": date_mention_id,
        "PeopleGroupID": people_group_id,
        "PeopleGroupMentionID": people_group_mention_id,
        "PeopleGroup_Name": people_group_name,
        "is_map": None,
        "map_vision_checked_at": None,
        "extracted_date": datetime.utcnow().isoformat() + "Z",
    }


def external_map_fields(
    *,
    external_source: Optional[str],
    external_source_url: Optional[str],
    archive_id: Optional[str] = None,
    license_: Optional[str] = None,
    license_url: Optional[str] = None,
    date_created: Optional[str] = None,
    creator: Optional[str] = None,
    found_via: Optional[str] = None,
    found_date: Optional[str] = None,
) -> Dict[str, Any]:
    """The fields shared by the EXTERNAL map sources (curated `external_maps` + web `openserp_maps`),
    which were identical copy-paste between them."""
    return {
        "external_source": external_source,
        "external_source_url": external_source_url,
        "archive_id": archive_id,
        "license": license_,
        "license_url": license_url,
        "date_created": date_created,
        "creator": creator,
        "found_via": found_via,
        "found_date": found_date,
    }


def _dedup_references(existing: list, incoming: list) -> tuple:
    """Merge incoming reference footprints into existing, deduping by (book, paragraph_number,
    Sub_eventID). Returns (merged_list, num_added)."""

    def _key(r: Dict[str, Any]) -> tuple:
        return (
            r.get("book") or r.get("source_book") or "",
            r.get("paragraph_number"),
            r.get("Sub_eventID") or r.get("sub_event_id") or "",
        )

    seen = {_key(r) for r in existing}
    added = 0
    merged = list(existing)
    for r in incoming or []:
        k = _key(r)
        if k not in seen:
            merged.append(r)
            seen.add(k)
            added += 1
    return merged, added


def find_or_create_map_by_url(
    *,
    url: str,
    maps_dir: Path,
    url_index: Dict[str, str],
    references: list,
    build_record,
) -> Optional[Path]:
    """URL-keyed find-or-create for a Map (cross-chapter shared maps — roman-numeral campaign
    foldouts are referenced from many chapters/books). Identity = the map URL.

    - EXISTING (url in url_index): APPEND the new chapter's reference footprint to the existing
      record's ``references[]`` (deduped), rewrite it, OBSERV maps.shared_appended. No duplicate.
    - NEW: ``build_record(map_id)`` must return the full record dict; mint the MapID, write it,
      register url -> filename, OBSERV maps.created.

    Returns the map file path (or None on a contained-path failure). Never raises into extraction.
    """
    from src.utils.file_lock import write_json_with_lock  # noqa: F401 (used by helpers)

    key = (url or "").strip()
    if not key:
        return None
    if key in url_index:
        return _append_shared_map(key, maps_dir, url_index, references)
    return _create_new_map(key, maps_dir, url_index, references, build_record)


def _append_shared_map(
    key: str, maps_dir: Path, url_index: Dict[str, str], references: list
) -> Optional[Path]:
    """EXISTING map: append this chapter's footprint to the shared record (deduped). Containment on
    READ too (defense-in-depth) — a crafted index value (if ever rehydrated from untrusted input)
    is refused, not path-joined blindly."""
    from src.utils.file_lock import write_json_with_lock
    from src.extraction.batch_parallel import _contained_entity_path

    existing_file = _contained_entity_path(maps_dir, Path(url_index[key]).name)
    if existing_file is None:
        logger.warning(
            "OBSERV maps.shared_path_unsafe url=%s value=%s status=warn",
            key[:120],
            str(url_index[key])[:120],
        )
        return None
    try:
        rec = json.loads(existing_file.read_text(encoding="utf-8"))
    except (
        Exception
    ) as e:  # noqa: BLE001 - a corrupt existing record is loud, not fatal
        logger.warning(
            "OBSERV maps.shared_read_failed url=%s file=%s reason=%s status=warn",
            key[:120],
            url_index[key],
            type(e).__name__,
        )
        return existing_file
    merged, added = _dedup_references(rec.get("references") or [], references)
    if added:
        rec["references"] = merged
        write_json_with_lock(existing_file, rec)
        logger.info(
            "OBSERV maps.shared_appended url=%s map_id=%s refs_added=%d status=ok",
            key[:120],
            rec.get("MapID"),
            added,
        )
    return existing_file


def _create_new_map(
    key: str, maps_dir: Path, url_index: Dict[str, str], references: list, build_record
) -> Optional[Path]:
    """NEW map: mint MapID, build + write the record, register url -> filename. Path-traversal safe
    (server-minted ULID filename + contained path)."""
    from src.utils.file_lock import write_json_with_lock
    from src.extraction.batch_parallel import (
        _safe_entity_filename,
        _contained_entity_path,
    )

    map_id = str(ulid.new())
    record = build_record(map_id)
    record.setdefault("references", references or [])
    filename = _safe_entity_filename(f"{map_id}.json", f"map_{map_id[:12]}")
    map_file = _contained_entity_path(maps_dir, filename)
    if map_file is None:
        observe_warn("maps", "create_path_unsafe", url=key[:120])
        return None
    write_json_with_lock(map_file, record)
    url_index[key] = map_file.name
    logger.info(
        "OBSERV maps.created url=%s map_id=%s refs=%d status=ok",
        key[:120],
        map_id,
        len(references or []),
    )
    return map_file


# Default map-type vocabulary (source-agnostic; config may override via classify_map_type's keywords).
# Keyed broad->specific; first match wins. Deterministic keyword classification (no LLM).
_MAP_TYPE_VOCAB = {
    "situation": ("situation", "disposition", "front line", "the front"),
    "operational": (
        "operation",
        "campaign",
        "advance",
        "offensive",
        "assault",
        "landing",
    ),
    "tactical": ("tactical", "sector", "beach", "bridgehead", "crossing"),
    "strategic": ("strategic", "theater", "theatre", "overview", "europe"),
    "terrain": ("terrain", "relief", "topograph", "elevation"),
}


def classify_map_type(title: str, context: str = "") -> Optional[str]:
    """Deterministic map-type classification from the title/context text (no LLM). Source-agnostic
    keyword vocab; returns the first matching type or None (unknown is honest, not guessed).
    """
    text = f"{title or ''} {context or ''}".lower()
    for map_type, terms in _MAP_TYPE_VOCAB.items():
        if any(t in text for t in terms):
            return map_type
    return None


def download_map_image(url: str, map_id: str, files_dir: Path) -> tuple:
    """SSRF-safe download of a map image to ``files_dir/{map_id}.{ext}`` (reuses the central
    images.fetch_image_bytes gate). Returns (local_image_path|None, file_format|None). Fail-safe:
    a blocked/non-image/failed download returns (None, None) with a loud OBSERV — never raises.
    """
    from src.extraction.images import fetch_image_bytes
    from src.extraction.batch_parallel import _contained_entity_path

    try:
        body, ext = fetch_image_bytes(url)
    except Exception as e:  # noqa: BLE001 - download failure is loud, not fatal
        logger.warning(
            "OBSERV maps.download_failed url=%s reason=%s status=warn",
            (url or "")[:120],
            type(e).__name__,
        )
        return None, None
    if not body:
        logger.warning(
            "OBSERV maps.download_blocked url=%s status=warn (non-image/blocked)",
            (url or "")[:120],
        )
        return None, None
    ext = (ext or "jpg").lstrip(".")
    files_dir.mkdir(parents=True, exist_ok=True)
    out = _contained_entity_path(files_dir, f"{map_id}.{ext}")
    if out is None:
        observe_warn("maps", "download_path_unsafe", map_id=map_id)
        return None, None
    out.write_bytes(body)
    return str(out), ext


def _sub_event_ids(references: list) -> set:
    """Collect the Sub_eventIDs from a map's reference footprint (Step 1 captures them)."""
    out = set()
    for r in references or []:
        sid = r.get("Sub_eventID") or r.get("sub_event_id")
        if sid:
            out.add(sid)
    return out


def _resolve_entity_for_subevents(
    entity_dir: Path, sub_event_ids: set, id_field: str, name_field: str
) -> tuple:
    """Find the FIRST entity whose event_mentions share any of the map's sub-events. Returns
    (entity_id, name) or (None, None). Generic over places/dates/people_groups (reuses the
    index.json + event_mentions[].Sub_eventID matching pattern). Read-only."""
    index_path = entity_dir / "index.json"
    if not index_path.exists() or not sub_event_ids:
        return None, None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt index yields no link, not a crash
        return None, None
    from src.extraction.batch_parallel import _contained_entity_path

    for fname in index.values():
        # Containment + basename strip (parity with the map read-path hardening): a poisoned index
        # value (traversal/absolute/symlink-escape) is refused, not followed. index values are
        # pipeline-written basenames today; this is defense-in-depth consistency.
        f = _contained_entity_path(entity_dir, Path(str(fname)).name)
        if f is None or not f.exists():
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for m in rec.get("event_mentions", []):
            if m.get("Sub_eventID") in sub_event_ids:
                return rec.get(id_field), rec.get(name_field)
    return None, None


def link_map_entities(
    record: Dict[str, Any],
    output_root: Path,
) -> None:
    """Step 5c-i — stamp Place/Date/Organization links onto a Map record from its reference-footprint
    sub-events (deterministic, no Grok). Mutates `record` in place. Only fills a link that is
    currently empty (idempotent on re-run). OBSERV maps.linked with the dimensions resolved.
    """
    sids = _sub_event_ids(record.get("references") or [])
    if not sids:
        return
    linked = []
    if not record.get("PlaceMentionID"):
        pid, pname = _resolve_entity_for_subevents(
            output_root / "places", sids, "PlaceID", "current_name"
        )
        if pid:
            record["PlaceMentionID"], record["place_name"] = pid, pname
            linked.append("place")
    if not record.get("DateMentionID"):
        did, dname = _resolve_entity_for_subevents(
            output_root / "dates", sids, "DateID", "date_start"
        )
        if did:
            record["DateMentionID"], record["date"] = did, dname
            linked.append("date")
    if not record.get("PeopleGroupID"):
        gid, gname = _resolve_entity_for_subevents(
            output_root / "people_groups", sids, "GroupID", "group_name"
        )
        if gid:
            record["PeopleGroupID"], record["PeopleGroup_Name"] = gid, gname
            linked.append("organization")
    if linked:
        logger.info(
            "OBSERV maps.linked map_id=%s dims=%s status=ok",
            record.get("MapID"),
            ",".join(linked),
        )


# --- Tiered map classification + idempotent Grok-vision backstop (5c-ii) --------------------------
# The deterministic tier comes from the ONE shared classifier (src/extraction/media_classify):
# ibiblio URL paths decide map/not-map; every other host is AMBIGUOUS and falls back to the
# Grok-vision "is this an image of a map?" classifier (prompts/map_vision STEP-1). The vision call
# is IDEMPOTENT: it runs ONLY when the record has no map_vision_checked_at; a re-run never
# re-submits an already-classified image (the don't-resubmit-maps-to-Grok TODO).


def classify_image_tier(name: str, url: str) -> str:
    """Deterministic tier: 'map' (tier 1), 'not_map' (tier 2), or 'ambiguous' (tier 3 -> vision)."""
    from src.extraction.media_classify import classify_media

    kind = classify_media(name, url).kind
    return {"map": "map", "image": "not_map"}.get(kind, "ambiguous")


def maybe_vision_classify_map(
    record: Dict[str, Any], image_bytes_loader, grok_client
) -> None:
    """Set record['is_map'] via the tier model, using the Grok-vision backstop ONLY for AMBIGUOUS
    images and ONLY once (idempotent). Mutates record in place. image_bytes_loader() -> bytes|None
    is called lazily (only for the tier-3 vision path). Fail-safe; never raises into the phase.
    """
    import datetime as _dt

    if record.get("map_vision_checked_at"):
        return  # already classified -> never re-submit (don't-resubmit guarantee)
    tier = classify_image_tier(
        str(
            record.get("common_name")
            or record.get("map_title")
            or record.get("figure_number")
            or ""
        ),
        str(record.get("source_url") or ""),
    )
    if tier == "map":
        record["is_map"] = True
        logger.info(
            "OBSERV maps.tier map_id=%s tier=1 is_map=true status=ok",
            record.get("MapID"),
        )
        return
    if tier == "not_map":
        record["is_map"] = False
        logger.info(
            "OBSERV maps.tier map_id=%s tier=2 is_map=false status=ok",
            record.get("MapID"),
        )
        return
    # Tier 3 — ambiguous: the Grok-vision backstop. Stamp checked_at ONLY when vision DECIDED — no
    # image yet / vision error / 'unknown' leaves it UNSET so a later pass re-classifies it (the
    # backstop must never silently self-disable, nor record a failed call as a verdict).
    vision_ran = _vision_classify_ambiguous(record, image_bytes_loader, grok_client)
    if vision_ran:
        record["map_vision_checked_at"] = _dt.datetime.utcnow().isoformat() + "Z"


def _vision_classify_ambiguous(
    record: Dict[str, Any], image_bytes_loader, grok_client
) -> bool:
    """Tier-3 Grok-vision 'is this an image of a map?' via the shared media_classify.vision_is_map.
    Returns True iff vision DECIDED (is_map set) — the caller stamps map_vision_checked_at only
    then. No image, no client, a vision error or an 'unknown' answer returns False: is_map stays
    unset and the record stays re-classifiable on the next run (deferred, never silently dropped),
    with a loud OBSERV line.
    """
    try:
        body = image_bytes_loader()
    except Exception as e:  # noqa: BLE001
        body = None
        logger.warning(
            "OBSERV maps.vision_load_failed map_id=%s reason=%s status=warn",
            record.get("MapID"),
            type(e).__name__,
        )
    if not body:
        logger.warning(
            "OBSERV maps.vision_skipped map_id=%s reason=no_image status=warn",
            record.get("MapID"),
        )
        return False  # no image -> NOT attempted; stays re-classifiable on a later pass
    from src.extraction.media_classify import vision_is_map

    is_map, caption = vision_is_map(
        body,
        {
            "alt_text": str(record.get("map_title") or record.get("common_name") or ""),
            "place_name": str(record.get("place_name") or ""),
            "date": str(record.get("date") or ""),
            "sub_event_name": str(
                record.get("PeopleGroup_Name") or record.get("Event_Name") or ""
            ),
        },
        grok_client,
    )
    if is_map is None:
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "maps",
            "vision_undecided",
            key=(getattr(caption, "method", "not_attempted"),),
            note=(
                "map vision could not classify; these maps stay unclassified and are retried "
                "next run — check XAI_API_KEY / Grok errors in this task's log"
            ),
            map_id=record.get("MapID"),
        )
        return False
    record["is_map"] = is_map
    logger.info(
        "OBSERV maps.tier map_id=%s tier=3 is_map=%s status=ok",
        record.get("MapID"),
        is_map,
    )
    return True
