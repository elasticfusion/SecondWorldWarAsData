"""Corpus referential-integrity: the single source of the cross-reference edge model + checker.

Core (not a throwaway utility): both the read-only CLI audit (`scripts/referential_integrity_audit.py`)
and the end-of-phase ENFORCEMENT pass import from here, so the edge map + resolution logic can never
drift. The per-write guard validates each record in isolation and is structurally blind to dangling
cross-references (a `source_section.EventID` pointing at an event that was never persisted); this
module is the complement that resolves references across the whole corpus AFTER all writes (so a
same-run create-order between an event and its referrer is a non-issue).
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

# EventID/Sub-eventID both resolve to the event key-space (events have no `required_id` in the
# registry because they are keyed inside the nested Event object, not a flat PK field).
_EVENT_PK_FIELDS = ("EventID", "Sub-eventID")


def _pk_field_to_entity() -> Dict[str, str]:
    """Map each primary-key field name -> owning entity, derived from the registry (single source).

    e.g. ``PersonID -> people``. A reference field anywhere in any schema that uses one of these
    names resolves to that entity's PK set. Includes the event key-space fields explicitly.
    """
    from src.schemas.entity_registry import ENTITY_REGISTRY

    mapping: Dict[str, str] = {}
    for spec in ENTITY_REGISTRY:
        if spec.required_id:
            mapping[spec.required_id] = spec.name
    for f in _EVENT_PK_FIELDS:
        mapping[f] = "events"
    return mapping


def _walk_id_fields(node: Any, path: str = "") -> List[str]:
    """Yield every ``*ID`` field path in a JSON Schema, descending into object properties and
    array ``items`` (list segments marked with a trailing ``[]``)."""
    out: List[str] = []
    if not isinstance(node, dict):
        return out
    for key, val in (node.get("properties") or {}).items():
        p = f"{path}.{key}" if path else key
        if key.endswith("ID"):
            out.append(p)
        if isinstance(val, dict):
            if val.get("type") == "array" and isinstance(val.get("items"), dict):
                out.extend(_walk_id_fields(val["items"], p + "[]"))
            else:
                out.extend(_walk_id_fields(val, p))
    return out


def _discover_edges() -> List[Tuple[str, str, str, bool]]:
    """Derive the reference edges from the registry schemas (single source — cannot drift).

    An edge is any schema field whose leaf name is a known PK (``PersonID``, ``EventID``, ...),
    EXCEPT the entity's own primary key. ``*MentionID`` and other non-PK ``*ID`` names (e.g.
    ``maps.Sub_eventID`` with an underscore) are not PKs, so they are naturally excluded (they
    resolve to no PK set). All reference nullable (null/absent reference is allowed)."""
    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    pk_to_entity = _pk_field_to_entity()
    edges: List[Tuple[str, str, str, bool]] = []
    for spec in ENTITY_REGISTRY:
        try:
            schema = load_schema(spec)
        except Exception:  # noqa: BLE001
            continue
        own_pk = spec.required_id
        for field_path in _walk_id_fields(schema):
            leaf = field_path.split(".")[-1].replace("[]", "")
            if leaf == own_pk:
                continue  # the record's own primary key is not a cross-reference
            target = pk_to_entity.get(leaf)
            if not target or target == spec.name:
                continue  # non-PK (*MentionID etc.) or a self-PK reference
            # Events live in nested files; the 'events' entity's own fields are the key-space.
            if spec.name == "events":
                continue
            edges.append((spec.name, field_path, target, True))
    return edges


def _pk_targets() -> Dict[str, str]:
    """entity name -> PK field, for every registry entity that has one (builds the resolvable
    PK sets). ``people_groups`` keeps its registry name as the entity key."""
    from src.schemas.entity_registry import ENTITY_REGISTRY

    return {s.name: s.required_id for s in ENTITY_REGISTRY if s.required_id}


# Derived once at import (single source of truth: the registry + schemas).
EDGES: List[Tuple[str, str, str, bool]] = _discover_edges()
PK_FIELD: Dict[str, str] = _pk_targets()
_DIR: Dict[str, str] = {}  # registry entity names already equal their output subdir


def _iter_records(storage: Any, entity_dir: str):
    """Yield (key, data) for each entity JSON (skip index/report/dot files)."""
    for rel in storage.list_files(entity_dir, "*.json"):
        name = rel.rsplit("/", 1)[-1]
        if name == "index.json" or name.startswith(".") or "report" in name:
            continue
        try:
            yield rel, storage.read_json(rel)
        except Exception:  # noqa: BLE001
            continue


def _event_files(storage: Any) -> List[str]:
    """Event files nested under content/<book>/*-event.json."""
    out: List[str] = []
    try:
        for rel in storage.list_files("content", "*.json"):
            if rel.endswith("-event.json"):
                out.append(rel)
    except Exception:  # noqa: BLE001
        pass
    return out


def build_pk_sets(storage: Any) -> Dict[str, Set[str]]:
    """Collect valid primary keys per target type (+ EventID/Sub-eventID from event files)."""
    sets: Dict[str, Set[str]] = defaultdict(set)
    for name, pk in PK_FIELD.items():
        for _k, data in _iter_records(storage, _DIR.get(name, name)):
            v = data.get(pk)
            if isinstance(v, str) and v:
                sets[name].add(v)
    for rel in _event_files(storage):
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


def resolve_path(data: Dict[str, Any], path: str) -> List[Any]:
    """Resolve a dotted field path with optional '[]' list segments to a flat value list."""
    nodes: List[Any] = [data]
    for seg in path.split("."):
        is_list = seg.endswith("[]")
        key = seg[:-2] if is_list else seg
        nxt: List[Any] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            val = n.get(key)
            if is_list and isinstance(val, list):
                nxt.extend(val)
            elif not is_list and val is not None:
                nxt.append(val)
        nodes = nxt
    return nodes


def audit(storage: Any, include_samples: bool = False) -> Dict[str, Any]:
    """Resolve every edge; return per-edge dangling counts + totals. Read-only.

    ``include_samples`` (default False) controls whether up to 3 raw dangling ID VALUES are
    attached per edge. Raw IDs are record identifiers; keep them OFF on any alerting/sync path
    (aggregate counts only) and enable only for a local operator diagnosis."""
    pk_sets = build_pk_sets(storage)
    results: List[Dict[str, Any]] = []
    for src, field, target, _nullable in EDGES:
        total = dangling = 0
        samples: List[str] = []
        valid = pk_sets.get(target, set())
        for _k, data in _iter_records(storage, _DIR.get(src, src)):
            for val in resolve_path(data, field):
                if not isinstance(val, str) or not val:
                    continue  # null/absent reference is allowed
                total += 1
                if val not in valid:
                    dangling += 1
                    if include_samples and len(samples) < 3:
                        samples.append(val)
        results.append(
            {
                "edge": f"{src}.{field} -> {target}",
                "total_refs": total,
                "dangling": dangling,
                "rate": round(dangling / total, 4) if total else 0.0,
                "sample_dangling": samples,
            }
        )
    results.sort(key=lambda r: r["dangling"], reverse=True)
    return {
        "pk_counts": {k: len(v) for k, v in pk_sets.items()},
        "edges": results,
        "total_dangling": sum(r["dangling"] for r in results),
    }


def enforce(
    storage: Any, alert_topic_arn: str = "", region: str = "us-east-1"
) -> Dict[str, Any]:
    """End-of-phase ENFORCEMENT pass: run the audit after all writes, log a summary, and — if
    any dangling references exist — ALERT the operator (email/Slack via SNS). Returns the report.
    Order-independent + all-objects (the detective/enforcement complement to the write guard).
    Fail-safe: never raises into the pipeline."""
    try:
        report = audit(storage)
    except Exception as e:  # noqa: BLE001
        logger.warning("referential-integrity enforcement skipped (audit error): %s", e)
        return {"edges": [], "total_dangling": 0, "error": str(e)}

    total = report["total_dangling"]
    logger.info("Referential integrity: %d dangling cross-reference(s)", total)
    for edge in report["edges"]:
        if edge["dangling"]:
            logger.warning(
                "  dangling %s: %d/%d (%.1f%%)",
                edge["edge"],
                edge["dangling"],
                edge["total_refs"],
                edge["rate"] * 100,
            )
    if total and alert_topic_arn:
        _alert(report, alert_topic_arn, region)
    return report


def run_for_phase(output_root: Any, phase: str = "") -> Dict[str, Any]:
    """Single centralized entry point for Phase 1/2/3 end-of-phase enforcement.

    Resolves the storage backend (S3 when ``S3_BUCKET`` is set, else local ``output_root``) and the
    operator SNS topic the same way everywhere, then delegates to ``enforce``. Fail-safe: never
    raises into the pipeline (data-quality enforcement must not crash a phase)."""
    try:
        import os

        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        topic = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
        if os.environ.get("S3_BUCKET"):
            from src.utils.storage import S3Storage

            storage: Any = S3Storage(
                bucket=os.environ["S3_BUCKET"], prefix="output", region=region
            )
        else:
            from pathlib import Path

            from src.utils.storage import LocalStorage

            storage = LocalStorage(base_dir=Path(output_root))
        if phase:
            logger.info("Referential-integrity enforcement (%s)", phase)
        return enforce(storage, alert_topic_arn=topic, region=region)
    except Exception as e:  # noqa: BLE001
        logger.warning("referential-integrity enforcement skipped: %s", e)
        return {"edges": [], "total_dangling": 0, "error": str(e)}


def _alert(report: Dict[str, Any], topic_arn: str, region: str) -> None:
    """Email/Slack a dangling-reference alert (aggregate counts only; no record values)."""
    lines = [
        f"DATA QUALITY: {report['total_dangling']} dangling cross-reference(s) detected "
        "(a referrer points at a target record that does not exist).",
        "",
        "Edges (dangling / total):",
    ]
    for edge in report["edges"]:
        if edge["dangling"]:
            lines.append(
                f"  - {edge['edge']}: {edge['dangling']}/{edge['total_refs']} ({edge['rate'] * 100:.1f}%)"
            )
    try:
        import boto3

        boto3.client("sns", region_name=region).publish(
            TopicArn=topic_arn,
            Subject="WWII Pipeline: DATA QUALITY — dangling cross-references",
            Message="\n".join(lines),
        )
        logger.info("Sent dangling-reference alert")
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to send dangling-reference alert: %s", e)
