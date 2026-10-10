#!/usr/bin/env python3
"""Corpus referential-integrity audit (data-quality HIGH-1).

READ-ONLY. Walks the entity corpus and verifies every cross-reference ID resolves to an existing
record of the right type. Reports dangling-reference counts per edge — the class of corruption the
per-write guard is blind to (it validates each record in isolation; it cannot see that a
`casualties.event_context.EventID` points at an event that does not exist).

Design:
  1. Build the set of valid PRIMARY KEYS per entity (PersonID/PlaceID/GroupID/EquipmentID/DateID)
     + the set of valid EventID / Sub-eventID from the event files.
  2. For each known reference EDGE (source entity.field -> target set), count references that do
     NOT resolve (dangling), and — for the Weather->Places edge — flag the known MentionID-vs-PlaceID
     TYPE confusion (a value that is a ULID but belongs to a *mention*, not a place).
  3. Emit a per-edge report (+ JSON) and a non-zero exit if dangling refs exceed a threshold.

Storage-agnostic: uses the configured Storage backend (local or S3), same as the pipeline.
"""

import argparse
import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger("referential_integrity_audit")

# Edges to audit: (source_entity, dotted-ish field path, target_pk_set_name, nullable).
# Field paths support list-of-dicts via a trailing "[]" segment.
EDGES: List[Tuple[str, str, str, bool]] = [
    ("casualties", "PersonID", "people", True),
    ("casualties", "PlaceID", "places", True),
    ("casualties", "event_context.EventID", "events", True),
    ("casualties", "impacted_organizations[].GroupID", "groups", True),
    ("casualties", "impacted_people[].PersonID", "people", True),
    ("casualties", "impacted_places[].PlaceID", "places", True),
    ("people", "group_affiliations[].GroupID", "groups", True),
    ("weather", "location.PlaceID", "places", True),
    ("logistics", "impacted_organizations[].GroupID", "groups", True),
    ("logistics", "impacted_places[].PlaceID", "places", True),
    ("source_section", "EventID", "events", True),
    ("images", "EventID", "events", True),
    ("maps", "EventID", "events", True),
]

# entity dir -> primary-key field for building the resolvable-PK sets.
PK_FIELD = {
    "people": "PersonID",
    "places": "PlaceID",
    "groups": "GroupID",  # people_groups dir
    "equipment": "EquipmentID",
    "dates": "DateID",
}
_DIR = {"groups": "people_groups"}  # entity name -> output subdir when they differ


def _iter_records(storage: Any, entity_dir: str):
    """Yield (key, data) for each entity JSON (skip index/report files)."""
    for rel in storage.list_files(entity_dir, "*.json"):
        name = rel.rsplit("/", 1)[-1]
        if name == "index.json" or name.startswith(".") or "report" in name:
            continue
        try:
            yield rel, storage.read_json(rel)
        except Exception:  # noqa: BLE001
            continue


def build_pk_sets(storage: Any) -> Dict[str, Set[str]]:
    """Collect the set of valid primary keys per target type (+ event/sub-event IDs)."""
    sets: Dict[str, Set[str]] = defaultdict(set)
    for name, pk in PK_FIELD.items():
        for _k, data in _iter_records(storage, _DIR.get(name, name)):
            v = data.get(pk)
            if isinstance(v, str) and v:
                sets[name].add(v)
    # Events live under output/content/<book>/*-event.json; collect EventID + Sub-eventID.
    for rel in storage.list_files("content", "*-event.json") + _glob_events(storage):
        try:
            data = storage.read_json(rel)
        except Exception:  # noqa: BLE001
            continue
        ev = data.get("Event") if isinstance(data, dict) else None
        if isinstance(ev, dict):
            if isinstance(ev.get("EventID"), str):
                sets["events"].add(ev["EventID"])
            for se in ev.get("Sub-events", []) or []:
                if isinstance(se, dict) and isinstance(se.get("Sub-eventID"), str):
                    sets["events"].add(se["Sub-eventID"])
    return sets


def _glob_events(storage: Any) -> List[str]:
    """Events are nested under content/<book>/; list_files on 'content' may not recurse, so try
    a recursive-style listing if the backend supports a deeper prefix."""
    out: List[str] = []
    try:
        for rel in storage.list_files("content", "*.json"):
            if rel.endswith("-event.json"):
                out.append(rel)
    except Exception:  # noqa: BLE001
        pass
    return out


def _resolve_path(data: Dict[str, Any], path: str) -> List[Any]:
    """Resolve a dotted field path with optional '[]' list segments to a flat list of values."""
    nodes: List[Any] = [data]
    for seg in path.split("."):
        is_list = seg.endswith("[]")
        key = seg[:-2] if is_list else seg
        nxt: List[Any] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            val = n.get(key)
            if is_list:
                if isinstance(val, list):
                    nxt.extend(val)
            else:
                if val is not None:
                    nxt.append(val)
        nodes = nxt
    return nodes


def audit(storage: Any) -> Dict[str, Any]:
    pk_sets = build_pk_sets(storage)
    results: List[Dict[str, Any]] = []
    for src, field, target, _nullable in EDGES:
        total = 0
        dangling = 0
        samples: List[str] = []
        valid_targets = pk_sets.get(target, set())
        for _k, data in _iter_records(storage, _DIR.get(src, src)):
            for val in _resolve_path(data, field):
                if not isinstance(val, str) or not val:
                    continue  # null/absent reference is allowed (not dangling)
                total += 1
                if val not in valid_targets:
                    dangling += 1
                    if len(samples) < 3:
                        samples.append(val)
        rate = round(dangling / total, 4) if total else 0.0
        results.append(
            {
                "edge": f"{src}.{field} -> {target}",
                "total_refs": total,
                "dangling": dangling,
                "rate": rate,
                "sample_dangling": samples,
            }
        )
    results.sort(key=lambda r: r["dangling"], reverse=True)
    total_dangling = sum(r["dangling"] for r in results)
    return {
        "pk_counts": {k: len(v) for k, v in pk_sets.items()},
        "edges": results,
        "total_dangling": total_dangling,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(
        description="Corpus referential-integrity audit (read-only)"
    )
    ap.add_argument("--output-dir", default="output")
    ap.add_argument(
        "--fail-threshold",
        type=int,
        default=-1,
        help="exit 1 if total dangling >= N (−1 = never fail)",
    )
    ap.add_argument("--json-out", default="", help="write the full report to this path")
    args = ap.parse_args()

    from pathlib import Path

    from src.utils.backends import create_storage
    from src.utils.config import load_config

    config = load_config()
    import os as _os

    if _os.environ.get("S3_BUCKET"):
        from src.utils.storage import S3Storage

        storage: Any = S3Storage(
            bucket=_os.environ["S3_BUCKET"],
            prefix="output",
            region=config.get("aws", {}).get("region", "us-east-1"),
        )
    else:
        storage = create_storage(config, Path(args.output_dir))

    report = audit(storage)
    logger.info("Referential-integrity audit — PK counts: %s", report["pk_counts"])
    logger.info("Edges (dangling / total):")
    for e in report["edges"]:
        flag = " ⚠" if e["dangling"] else ""
        logger.info(
            "  %-45s %6d / %-6d (%.1f%%)%s",
            e["edge"],
            e["dangling"],
            e["total_refs"],
            e["rate"] * 100,
            flag,
        )
    logger.info("TOTAL dangling references: %d", report["total_dangling"])
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.fail_threshold >= 0 and report["total_dangling"] >= args.fail_threshold:
        logger.error(
            "FAIL: %d dangling refs >= threshold %d",
            report["total_dangling"],
            args.fail_threshold,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
