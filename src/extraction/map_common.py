"""Shared Map-record builders — the single home for the fields common to EVERY map source.

Step 2 of the maps refactor (consolidation): the source-document (`maps.py`), curated-external
(`external_maps.py`) and web-search (`openserp_maps.py`) builders each hand-wrote the SAME ~18
common Map fields (MapID, title, Event/Place/Date links, local_path, extracted_date, map_type,
storage_backend, ...). That is genuine copy-paste. This module owns the common skeleton; each
source-specific builder calls it and MERGES only its own fields (source_book/figure for the
source-document map; license/creator/external_source for the external/web maps). No behavior
change — the emitted keys/values are identical to the former inline dicts.
"""

from datetime import datetime
from typing import Any, Dict, Optional


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
