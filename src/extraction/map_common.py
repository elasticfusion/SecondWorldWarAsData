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
        logger.warning("OBSERV maps.create_path_unsafe url=%s status=warn", key[:120])
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
