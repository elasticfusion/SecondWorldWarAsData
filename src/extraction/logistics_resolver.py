"""Shared logistics resolution: link an entity (e.g. a casualty) to a LogisticsID.

Two tiers, both honestly labeled by an `association`:
- **explicit** — the source/LLM directly names a logistics reference (strong); resolved by
  LogisticsID (if given) or by description match.
- **co_occurring** — a *candidate* link inferred when the entity shares a GroupID AND an
  overlapping date with a logistics record (weak). The data does NOT model place on
  logistics and casualty `cause` is not yet populated corpus-wide, so this is a candidate
  for review, NEVER asserted as causation.

Reused by casualties cross-referencing (and available to other features). Logistics records
carry `temporal` (date_start/end, DateID_start/end), `logistics_type`, and
`impacted_organizations[].PeopleGroupID` — those are the join keys (no PlaceID exists on
logistics).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# logistics_type values that plausibly relate to personnel casualties.
SUPPLY_TYPES = {"supply_shortage", "delivery_delay", "transport_disruption"}


def build_logistics_index(logistics_dir: Path) -> Dict[str, Any]:
    """Build join indexes over logistics records:
    {'by_group': {GroupID: [entry]}, 'by_id': {LogisticsID: entry}} where entry carries
    LogisticsID, logistics_type, and the date interval [date_start, date_end]."""
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    by_id: Dict[str, Dict[str, Any]] = {}
    if not logistics_dir.exists():
        return {"by_group": by_group, "by_id": by_id}
    for f in logistics_dir.glob("*.json"):
        if f.name in ("index.json",):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # A logistics file may hold a single record (dict) OR a list of records.
        records = data if isinstance(data, list) else [data]
        for d in records:
            if not isinstance(d, dict):
                continue
            lid = d.get("LogisticsID")
            if not lid:
                continue
            temporal = d.get("temporal") if isinstance(d.get("temporal"), dict) else {}
            entry = {
                "LogisticsID": lid,
                "logistics_type": d.get("logistics_type"),
                "date_start": temporal.get("date_start"),
                "date_end": temporal.get("date_end") or temporal.get("date_start"),
                "date_ids": [
                    temporal.get("DateID_start"),
                    temporal.get("DateID_end"),
                ],
            }
            by_id[lid] = entry
            for org in d.get("impacted_organizations", []) or []:
                gid = org.get("PeopleGroupID") if isinstance(org, dict) else None
                if gid:
                    by_group.setdefault(gid, []).append(entry)
    return {"by_group": by_group, "by_id": by_id}


def _date_overlaps(
    iso: Optional[str], start: Optional[str], end: Optional[str]
) -> bool:
    """True if an ISO date falls within [start, end] (string compare, ISO-safe). If the
    entity has no date, co-occurrence can't be time-confirmed -> False."""
    if not iso or not start:
        return False
    return start <= iso <= (end or start)


def resolve_logistics(
    refs: List[Any],
    group_ids: List[str],
    date_iso: Optional[str],
    index: Dict[str, Any],
    supply_only: bool = True,
    date_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return impacted_logistics[] links for an entity.

    `refs` are explicit logistics references the LLM provided (dicts with LogisticsID or a
    description). The inferred co-occurring tier fires when the entity shares a GroupID with
    a logistics record AND they co-occur in time — either a shared DateID or an ISO date
    within the logistics interval. Each link: {LogisticsID, logistics_type, association:
    explicit|co_occurring}. Explicit refs that don't resolve keep a null LogisticsID.
    """
    by_id = index.get("by_id", {})
    by_group = index.get("by_group", {})
    out: Dict[str, Dict[str, Any]] = {}

    # Tier 1: explicit references.
    for ref in refs or []:
        if isinstance(ref, str):
            ref = {"description": ref}
        if not isinstance(ref, dict):
            continue
        lid = ref.get("LogisticsID")
        if lid and lid in by_id:
            out[lid] = {
                "LogisticsID": lid,
                "logistics_type": by_id[lid]["logistics_type"],
                "association": "explicit",
            }
        else:
            out[f"desc:{ref.get('description','')}"] = {
                "LogisticsID": lid,
                "description": ref.get("description"),
                "association": "explicit",
            }

    # Tier 2: inferred co-occurring candidates (shared GroupID + co-occurrence in time).
    for gid in group_ids or []:
        for entry in by_group.get(gid, []):
            lid = entry["LogisticsID"]
            if lid in out:
                continue
            if supply_only and entry["logistics_type"] not in SUPPLY_TYPES:
                continue
            co_time = (
                date_id is not None and date_id in (entry.get("date_ids") or [])
            ) or _date_overlaps(date_iso, entry["date_start"], entry["date_end"])
            if co_time:
                out[lid] = {
                    "LogisticsID": lid,
                    "logistics_type": entry["logistics_type"],
                    "association": "co_occurring",
                }
    return list(out.values())
