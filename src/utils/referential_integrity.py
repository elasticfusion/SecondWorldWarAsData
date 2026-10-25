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
# registry because they are keyed inside the nested Event object, not a flat PK field). The
# underscore ``Sub_eventID`` is a real referrer-side variant (e.g. maps) — alias it so those edges
# are enforced rather than silently dropped.
_EVENT_PK_FIELDS = ("EventID", "Sub-eventID", "Sub_eventID")

# Referrer-side field-name aliases that point at an entity whose PK field is spelled differently.
# ``PeopleGroupID`` is the widely-used alias for a group reference (casualties/equipment/supplemental
# all emit it; equipment.py treats GroupID and PeopleGroupID as interchangeable), but the groups PK
# field is ``GroupID`` — alias it so those edges resolve to the group key-space.
_REF_ALIASES = {"PeopleGroupID": "people_groups"}


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
    mapping.update(_REF_ALIASES)
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
            # `type` may be a scalar ("array") or a union list (["array", "null"]).
            vtype = val.get("type")
            types = vtype if isinstance(vtype, list) else [vtype]
            if "array" in types and isinstance(val.get("items"), dict):
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
    """Yield (key, data) for each entity JSON dict (skip index/report/dot files, and skip any file
    whose top-level JSON is NOT a dict — e.g. ``bibliography/review_queue.json`` is a list — so a
    non-record artifact can never crash the pass)."""
    for rel in storage.list_files(entity_dir, "*.json"):
        name = rel.rsplit("/", 1)[-1]
        if name == "index.json" or name.startswith(".") or "report" in name:
            continue
        try:
            data = storage.read_json(rel)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            yield rel, data


def _event_files(storage: Any) -> List[str]:
    """Event files nested under ``content/<book>/*-event.json``. Local filesystem listing is not
    guaranteed recursive, so try a recursive-style pattern first and fall back to the flat one; a
    missing event key-space would make every event reference look dangling."""
    out: List[str] = []
    for pattern in ("*/*-event.json", "*-event.json", "*.json"):
        try:
            for rel in storage.list_files("content", pattern):
                if rel.endswith("-event.json"):
                    out.append(rel)
        except Exception:  # noqa: BLE001
            continue
        if out:
            return out
    return out


def build_pk_sets(storage: Any) -> Dict[str, Set[str]]:
    """Collect valid primary keys per target type (+ EventID/Sub-eventID from event files)."""
    sets: Dict[str, Set[str]] = defaultdict(set)
    for name, pk in PK_FIELD.items():
        for _k, data in _iter_records(storage, _DIR.get(name, name)):
            v = data.get(pk)
            if isinstance(v, str) and v:
                sets[name].add(v)
    _add_event_pks(storage, sets)
    return sets


def _add_event_pks(storage: Any, sets: Dict[str, Set[str]]) -> None:
    """Add EventID + Sub-eventID from the nested content/<book>/*-event.json files to
    the ``events`` PK set (shared by build_pk_sets and the single-read audit)."""
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

    SINGLE-READ: every entity record is fetched from storage EXACTLY ONCE. The old
    implementation read the whole corpus once to build PK sets and then AGAIN once
    per EDGE (O(edges x files) S3 GETs) — thousands of redundant round-trips that
    stalled the per-phase call. We now buffer each dir's records on the one read,
    build the PK sets, then resolve every reference from the in-memory buffer.

    ``include_samples`` (default False) controls whether up to 3 raw dangling ID VALUES are
    attached per edge. Raw IDs are record identifiers; keep them OFF on any alerting/sync path
    (aggregate counts only) and enable only for a local operator diagnosis."""
    # One read per source dir referenced by any edge (plus PK dirs). Buffer records.
    buffered = _buffer_edge_records(storage)

    # PK sets from the SAME buffered records (no second read) + event key-space.
    pk_sets = _build_pk_sets(storage, buffered)

    results: List[Dict[str, Any]] = []
    for src, field, target, _nullable in EDGES:
        results.append(
            _resolve_edge(
                buffered.get(src, []), field, src, target, pk_sets, include_samples
            )
        )
    results.sort(key=lambda r: r["dangling"], reverse=True)
    return {
        "pk_counts": {k: len(v) for k, v in pk_sets.items()},
        "edges": results,
        "total_dangling": sum(r["dangling"] for r in results),
    }


def _buffer_edge_records(storage: Any) -> "Dict[str, List[Dict[str, Any]]]":
    """Single-read buffer: one GET per source dir referenced by any edge (plus PK dirs)."""
    src_dirs = {src for src, _f, _t, _n in EDGES} | set(PK_FIELD)
    buffered: Dict[str, List[Dict[str, Any]]] = {}
    for name in src_dirs:
        buffered[name] = [
            data for _k, data in _iter_records(storage, _DIR.get(name, name))
        ]
    return buffered


def _build_pk_sets(
    storage: Any, buffered: "Dict[str, List[Dict[str, Any]]]"
) -> "Dict[str, Set[str]]":
    """PK sets from the already-buffered records (no second read) + the event key-space."""
    pk_sets: Dict[str, Set[str]] = defaultdict(set)
    for name, pk in PK_FIELD.items():
        for data in buffered.get(name, []):
            v = data.get(pk)
            if isinstance(v, str) and v:
                pk_sets[name].add(v)
    _add_event_pks(storage, pk_sets)
    return pk_sets


def _resolve_edge(
    src_records: "List[Dict[str, Any]]",
    field: str,
    src: str,
    target: str,
    pk_sets: "Dict[str, Set[str]]",
    include_samples: bool,
) -> "Dict[str, Any]":
    """Resolve one edge against the in-memory buffer: count total vs dangling references (a ref
    whose value is absent from the target PK set), with up to 3 sample dangling IDs when asked.
    """
    total = dangling = 0
    samples: List[str] = []
    valid = pk_sets.get(target, set())
    for data in src_records:
        for val in resolve_path(data, field):
            if not isinstance(val, str) or not val:
                continue  # null/absent reference is allowed
            total += 1
            if val not in valid:
                dangling += 1
                if include_samples and len(samples) < 3:
                    samples.append(val)
    return {
        "edge": f"{src}.{field} -> {target}",
        "total_refs": total,
        "dangling": dangling,
        "rate": round(dangling / total, 4) if total else 0.0,
        "sample_dangling": samples,
    }


_SUBEVENT_FIELDS = ("Sub_eventID", "Sub-eventID")
# Registry entities whose records carry event_mentions with a sub-event reference that can go stale
# when events are re-extracted (LLM segmentation can mint different deterministic Sub-eventIDs).
_MENTION_REPAIR_ENTITIES = ("places", "people_groups", "dates", "people", "equipment")


def repair_dangling_subevents(storage: Any) -> int:
    """DOWN-BIND repair: for an entity's event_mention whose sub-event reference (Sub_eventID /
    Sub-eventID) is STALE (not in the current event key-space) BUT whose parent EventID still
    RESOLVES, blank the stale sub-event id — keeping the valid event-level link instead of leaving a
    dangling reference. This handles the common re-extraction drift where events were regenerated
    with different deterministic Sub-eventIDs while the entity still points at the old generation.

    Only repairs when the EventID resolves (a mention with NO resolvable EventID is a real orphan,
    left for the audit/alert — not silently down-bound). Read-modify-write per changed record via
    the storage backend (write_json). Fail-safe: never raises; returns the number of mentions
    repaired."""
    try:
        valid = build_pk_sets(storage).get("events", set())
    except Exception as e:  # noqa: BLE001
        logger.warning("subevent repair skipped (pk-set build failed): %s", e)
        return 0
    if not valid:
        # No event key-space -> cannot distinguish stale from valid; do NOT blank anything.
        return 0

    repaired = 0
    for entity in _MENTION_REPAIR_ENTITIES:
        try:
            for rel, data in _iter_records(storage, _DIR.get(entity, entity)):
                n = _repair_record_subevents(data, valid)
                if not n:
                    continue
                # Count ONLY after a CONFIRMED schema-valid write. The storage write-guard silently
                # blocks a schema-invalid record (returns without raising), so pre-validate here and
                # skip (do NOT count) a record the guard would refuse — otherwise the repaired count
                # would be inflated + non-idempotent (re-claimed every run) when the blanked field
                # is not schema-legal for that entity.
                if not _repaired_record_is_writable(rel, data):
                    logger.warning(
                        "subevent repair SKIPPED (blanked record would be schema-blocked): %s",
                        rel,
                    )
                    continue
                try:
                    storage.write_json(rel, data)
                    repaired += n
                except Exception as e:  # noqa: BLE001
                    logger.warning("subevent repair write failed for %s: %s", rel, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("subevent repair skipped for %s: %s", entity, e)
    if repaired:
        logger.info(
            "OBSERV referential.subevent_repair repaired=%d (stale sub-event refs down-bound to "
            "their resolving parent EventID)",
            repaired,
        )
    return repaired


def _repaired_record_is_writable(rel: str, data: Dict[str, Any]) -> bool:
    """True if the repaired record would pass the central write-guard (so the storage write will
    actually persist). Fail-safe: on any guard-import/validation error, assume writable (the real
    write still runs — this pre-check only prevents counting a write the guard would silently drop).
    """
    try:
        from pathlib import Path as _P

        from src.utils.file_lock import _validate_entity

        return _validate_entity(_P(rel), data, record_stats=False)
    except Exception:  # noqa: BLE001
        return True


def _repair_record_subevents(data: Dict[str, Any], valid: Set[str]) -> int:
    """Blank stale sub-event refs in one record's event_mentions when the parent EventID resolves.
    Returns the count blanked. Mutates ``data`` in place."""
    mentions = data.get("event_mentions")
    if not isinstance(mentions, list):
        return 0
    n = 0
    for m in mentions:
        if not isinstance(m, dict):
            continue
        event_id = m.get("EventID")
        if not (isinstance(event_id, str) and event_id in valid):
            continue  # no resolvable parent -> a real orphan, leave for the audit (don't mask)
        for fld in _SUBEVENT_FIELDS:
            sid = m.get(fld)
            if isinstance(sid, str) and sid and sid not in valid:
                m[fld] = None  # down-bind to event level (keep the valid EventID)
                n += 1
    return n


def enforce(
    storage: Any, alert_topic_arn: str = "", region: str = "us-east-1"
) -> Dict[str, Any]:
    """End-of-phase ENFORCEMENT pass: run the audit after all writes, log a summary, and — if
    any dangling references exist — ALERT the operator (email/Slack via SNS). Returns the report.
    Order-independent + all-objects (the detective/enforcement complement to the write guard).
    Fail-safe: never raises into the pipeline."""
    # First DOWN-BIND any stale sub-event refs whose parent EventID still resolves (re-extraction
    # drift), so the audit/alert below reflects the true residual (real orphans only), not
    # re-extraction noise that we can safely repair.
    try:
        repair_dangling_subevents(storage)
    except (
        Exception
    ) as e:  # noqa: BLE001 - repair is best-effort; never block enforcement
        logger.warning("subevent down-bind repair skipped: %s", e)
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
    raises into the pipeline (data-quality enforcement must not crash a phase).

    BOUNDED: the audit enumerates the WHOLE output corpus, which on a large S3 bucket can take a
    long time. To guarantee it never STALLS a phase (observed: Phase-1 hung 8+ min on the full
    corpus, blocking the parsed-output sync + the next phase), this runs under a hard timeout and
    is skippable via SKIP_PHASE_REFERENTIAL_ENFORCE=1. On timeout/skip it returns a skipped report
    (the standalone corpus_audit job + the S3-validation backstop still cover whole-corpus
    auditing out-of-band)."""
    import os

    if os.environ.get("SKIP_PHASE_REFERENTIAL_ENFORCE") == "1":
        logger.info(
            "Referential-integrity enforcement SKIPPED (env) for %s", phase or "phase"
        )
        return {"edges": [], "total_dangling": 0, "skipped": "env"}

    timeout_s = int(os.environ.get("PHASE_REFERENTIAL_ENFORCE_TIMEOUT", "90"))
    result: Dict[str, Any] = {"edges": [], "total_dangling": 0, "skipped": "timeout"}

    def _run() -> None:
        nonlocal result
        try:
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
            result = enforce(storage, alert_topic_arn=topic, region=region)
        except Exception as e:  # noqa: BLE001
            logger.warning("referential-integrity enforcement skipped: %s", e)
            result = {"edges": [], "total_dangling": 0, "error": str(e)}

    import threading

    t = threading.Thread(target=_run, name="ref-integrity-enforce", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        logger.warning(
            "referential-integrity enforcement exceeded %ds for %s — skipping (the "
            "corpus audit runs out-of-band). Set PHASE_REFERENTIAL_ENFORCE_TIMEOUT to raise.",
            timeout_s,
            phase or "phase",
        )
    return result


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
