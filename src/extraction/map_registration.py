"""Reverse map registration: link narrative entities onto maps as a backdrop.

A map advertises a coverage EXTENT = {covered_places: [PlaceID]} x {date_range}. Any
narrative entity (person, casualty, logistics, equipment) that the pipeline resolved to a
(PlaceID, DateID) falling INSIDE a map's extent gets a back-link to that map — even though
the map never names the entity.

The link means "this entity is at a place+time this map depicts" (association
`spatial_temporal_coverage`), NOT "the map labels this entity". The join key is the entity
graph (PlaceID + DateID), so it is unaffected by map-OCR fuzziness.

Prototype-level: operates on output/ JSON records. Reusable across entity types.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def build_dateid_to_iso(dates_dir: Path) -> Dict[str, str]:
    """DateID -> date_start (ISO) for membership tests."""
    out: Dict[str, str] = {}
    if not dates_dir.exists():
        return out
    for f in dates_dir.glob("*.json"):
        if "index" in f.name:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        did, ds = d.get("DateID"), d.get("date_start")
        if did and ds:
            out[did] = ds
    return out


def load_map_extents(map_features_dir: Path) -> List[Dict[str, Any]]:
    """Load [{MapID, covered_places:set, earliest, latest}] from map-features files that
    carry a coverage extent."""
    extents: List[Dict[str, Any]] = []
    if not map_features_dir.exists():
        return extents
    for f in map_features_dir.glob("*.json"):
        if "index" in f.name:
            continue
        try:
            fc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        covered = set(fc.get("covered_places") or [])
        dr = fc.get("date_range") or {}
        if not covered:
            continue
        extents.append(
            {
                "MapID": fc.get("MapID") or f.stem,
                "covered_places": covered,
                "earliest": dr.get("earliest"),
                "latest": dr.get("latest"),
            }
        )
    return extents


def _in_date_range(
    iso: Optional[str], earliest: Optional[str], latest: Optional[str]
) -> bool:
    """True if iso falls within [earliest, latest]. If the map has no range, date is not a
    constraint (place-only coverage). If the entity has no date, it cannot be time-matched.
    """
    if earliest is None or latest is None:
        return True  # map is place-only; place membership alone qualifies
    if not iso:
        return False
    return earliest <= iso <= latest


def _iter_anchor_pairs(
    obj: Any, dateid_to_iso: Dict[str, str]
) -> Iterable[Tuple[str, Optional[str]]]:
    """Yield (PlaceID, iso_date_or_None) pairs found anywhere in an entity record.

    Collects all PlaceIDs and all DateID-derived dates present, then pairs each place with
    each date (a mention usually has one of each; cross-product is safe for membership).
    """
    pids: set = set()
    isos: set = set()

    def walk(o: Any):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "PlaceID" and isinstance(v, str) and v:
                    pids.add(v)
                elif k == "DateID" and isinstance(v, str) and v and v in dateid_to_iso:
                    isos.add(dateid_to_iso[v])
                else:
                    walk(v)
        elif isinstance(o, list):
            for item in o:
                walk(item)

    walk(obj)
    if not pids:
        return
    if not isos:
        for p in pids:
            yield p, None
    else:
        for p in pids:
            for iso in isos:
                yield p, iso


def register_entity_to_maps(
    record: Dict[str, Any], extents: List[Dict[str, Any]], dateid_to_iso: Dict[str, str]
) -> List[Dict[str, str]]:
    """Return [{MapID, association, PlaceID, date}] back-links for one entity record whose
    (PlaceID, DateID) anchors fall inside a map's extent. Empty if none match."""
    links: Dict[str, Dict[str, str]] = {}
    for pid, iso in _iter_anchor_pairs(record, dateid_to_iso):
        for ext in extents:
            if pid in ext["covered_places"] and _in_date_range(
                iso, ext["earliest"], ext["latest"]
            ):
                links[ext["MapID"]] = {
                    "MapID": ext["MapID"],
                    "association": "spatial_temporal_coverage",
                    "PlaceID": pid,
                    "date": iso or "",
                }
    return list(links.values())


def register_dir(
    entity_dir: Path, extents: List[Dict[str, Any]], dateid_to_iso: Dict[str, str]
) -> Dict[str, List[Dict[str, str]]]:
    """Scan an entity directory; return {record_filename: [map back-links]} for records
    that register onto at least one map. Does NOT mutate files (prototype/report mode).
    """
    result: Dict[str, List[Dict[str, str]]] = {}
    if not entity_dir.exists():
        return result
    for f in entity_dir.glob("*.json"):
        if "index" in f.name:
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        links = register_entity_to_maps(rec, extents, dateid_to_iso)
        if links:
            result[f.name] = links
    return result
