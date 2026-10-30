"""Provenance-COMPLETENESS audit — the complement to referential_integrity.

``referential_integrity`` answers "does every stored ID *resolve*?" (no dangling edges). It is
structurally blind to a DIFFERENT class of defect surfaced by the XChannel-8 E2E:

  1. a record that carries NO provenance edge at all (the link that *should* tie it back to its
     source section/event was never written — you cannot dangle a pointer that is absent); and
  2. attribution fields left as ``"TODO - Add ..."`` placeholders (a perfectly valid string, so
     both the per-write guard and the resolve-check pass it).

This module is SCHEMA-DRIVEN and covers EVERY registry entity: for each entity it derives which
provenance edge(s) its schema declares (``event_mentions[].EventID`` / ``EventID`` /
``SourceSectionID`` / ``mentions[].EventID``), then asserts each record carries at least one
POPULATED provenance edge, and scans for leftover TODO placeholders. Entities whose schema declares
NO direct source edge at all (e.g. casualties, bibliography) are reported as an explicit
weak-provenance anomaly rather than silently passing. Read-only, non-fatal — emits OBSERV + a
report, exactly like ``referential_integrity.audit``.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger(__name__)

# A record traces to source if ANY of these leaf fields is populated with a resolvable value.
_DIRECT_EVENT_FIELDS = ("EventID", "Sub-eventID")
_SECTION_FIELD = "SourceSectionID"
# Mention-array fields that carry an EventID per element (entity -> event provenance).
_MENTION_ARRAYS = ("event_mentions", "mentions")
# Indirect-only edges: resolve to another entity, not to the source directly (casualties).
_INDIRECT_FIELDS = ("PersonID", "PlaceID")

_PLACEHOLDER_RE = re.compile(r"TODO\s*-\s*Add", re.IGNORECASE)


def _schema_provenance_kind(schema: Dict[str, Any]) -> str:
    """Classify an entity schema's STRONGEST declared provenance edge.

    Returns one of: 'event_mention' | 'section' | 'event' | 'indirect' | 'none'.
    Schema-derived (single source of truth) so new entities are covered automatically.
    """
    fields = _walk_field_names(schema)
    if any(m in fields for m in _MENTION_ARRAYS):
        return "event_mention"
    if _SECTION_FIELD in fields:
        return "section"
    if any(f in fields for f in _DIRECT_EVENT_FIELDS):
        return "event"
    if any(f in fields for f in _INDIRECT_FIELDS):
        return "indirect"
    return "none"


def _walk_field_names(schema: Dict[str, Any]) -> Set[str]:
    """All property leaf-names in a JSON schema (descending objects + array items)."""
    out: Set[str] = set()
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    for k, v in props.items():
        out.add(k)
        if isinstance(v, dict):
            if v.get("type") == "object" or "properties" in v:
                out |= _walk_field_names(v)
            items = v.get("items")
            if isinstance(items, dict):
                out |= _walk_field_names(items)
    return out


def _record_has_populated_provenance(rec: Dict[str, Any]) -> bool:
    """True if the record carries at least one POPULATED (non-empty) provenance edge."""
    if _nonempty_str(rec.get(_SECTION_FIELD)):
        return True
    for f in _DIRECT_EVENT_FIELDS:
        if _nonempty_str(rec.get(f)):
            return True
    for arr in _MENTION_ARRAYS:
        items = rec.get(arr)
        if isinstance(items, list):
            for m in items:
                if isinstance(m, dict) and _nonempty_str(m.get("EventID")):
                    return True
    return False


def _record_has_indirect_provenance(rec: Dict[str, Any]) -> bool:
    """True if the record carries a populated INDIRECT edge (PersonID/PlaceID)."""
    return any(_nonempty_str(rec.get(f)) for f in _INDIRECT_FIELDS)


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _find_placeholders(node: Any, path: str = "") -> List[str]:
    """Paths of fields whose string value is a 'TODO - Add ...' placeholder. Recursive."""
    found: List[str] = []
    if isinstance(node, str):
        if _PLACEHOLDER_RE.search(node):
            found.append(path or "<root>")
    elif isinstance(node, dict):
        for k, v in node.items():
            found += _find_placeholders(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += _find_placeholders(v, f"{path}[{i}]")
    return found


def _iter_entity_records(out: Path, glob_pat: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Load (name, record) for an entity's output glob. Skips report/index sidecars. Fail-safe."""
    skip = {
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "review_queue.json",
        "related_groups_report.json",
    }
    recs: List[Tuple[str, Dict[str, Any]]] = []
    for f in out.glob(glob_pat):
        if f.name in skip or f.name.startswith("."):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            recs.append((f.name, data))
    return recs


def _audit_entity(
    name: str, kind: str, records: List[Tuple[str, Dict[str, Any]]]
) -> Dict[str, Any]:
    """Per-entity provenance audit: missing-edge count, placeholder count, indirect-only flag."""
    total = len(records)
    missing = 0
    placeholders = 0
    indirect_only = 0
    sample_missing: List[str] = []
    for fname, rec in records:
        if _record_has_populated_provenance(rec):
            pass
        elif kind == "indirect" and _record_has_indirect_provenance(rec):
            indirect_only += 1
        else:
            missing += 1
            if len(sample_missing) < 3:
                sample_missing.append(fname)
        if _find_placeholders(rec):
            placeholders += 1
    return {
        "entity": name,
        "kind": kind,
        "total": total,
        "missing_provenance": missing,
        "indirect_only": indirect_only,
        "placeholder_records": placeholders,
        "sample_missing": sample_missing,
    }


def audit(out: Path) -> Dict[str, Any]:
    """Schema-driven provenance-completeness audit across EVERY registry entity. Read-only.

    Returns a report dict; never raises. ``kind == 'none'`` entities are flagged structurally
    (their schema declares no direct source edge — a weak-provenance anomaly).
    """
    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    out = Path(out)
    results: List[Dict[str, Any]] = []
    structural_gaps: List[str] = []
    for spec in ENTITY_REGISTRY:
        if spec.name == "events" or not spec.output_glob:
            continue
        try:
            kind = _schema_provenance_kind(load_schema(spec))
        except Exception:  # noqa: BLE001
            continue
        if kind == "none":
            structural_gaps.append(spec.name)
        rel_glob = spec.output_glob.replace("output/", "", 1)
        records = _iter_entity_records(out, rel_glob)
        results.append(_audit_entity(spec.name, kind, records))

    total_missing = sum(r["missing_provenance"] for r in results)
    total_placeholder = sum(r["placeholder_records"] for r in results)
    return {
        "entities": results,
        "structural_gaps": structural_gaps,
        "total_missing_provenance": total_missing,
        "total_placeholder_records": total_placeholder,
    }


def run_for_phase(out: Path, phase: str = "") -> Dict[str, Any]:
    """End-of-phase provenance-completeness enforcement (non-fatal). Emits OBSERV + report."""
    try:
        report = audit(out)
    except Exception as e:  # noqa: BLE001 - enforcement must never crash a phase
        logger.debug("provenance audit skipped (%s): %s", phase, e)
        return {}

    logger.info(
        "OBSERV provenance.audit phase=%s missing=%d placeholders=%d structural_gaps=%s",
        phase or "?",
        report["total_missing_provenance"],
        report["total_placeholder_records"],
        ",".join(report["structural_gaps"]) or "none",
    )
    for r in report["entities"]:
        if r["missing_provenance"] or r["placeholder_records"] or r["kind"] == "none":
            logger.warning(
                "OBSERV provenance.entity entity=%s kind=%s total=%d missing=%d "
                "indirect_only=%d placeholders=%d sample=%s",
                r["entity"],
                r["kind"],
                r["total"],
                r["missing_provenance"],
                r["indirect_only"],
                r["placeholder_records"],
                ",".join(r["sample_missing"]) or "-",
            )
    # ALERT PARITY with referential_integrity: provenance anomalies were previously ONLY logged and
    # never surfaced to the operator. A missing-provenance record or a kind=='none' structural gap
    # is a real data-quality defect — emit a loud, greppable OBSERV alert AND (best-effort) an SNS
    # signal, exactly as referential_integrity surfaces dangling cross-references. Fail-safe.
    _maybe_alert_provenance(report, phase)
    return report


def _provenance_anomaly_total(report: Dict[str, Any]) -> int:
    """Count of provenance anomalies worth alerting on: missing-provenance records + placeholder
    records + structural (kind=='none') gaps. Zero => nothing to alert."""
    return (
        int(report.get("total_missing_provenance", 0))
        + int(report.get("total_placeholder_records", 0))
        + len(report.get("structural_gaps", []))
    )


def _maybe_alert_provenance(report: Dict[str, Any], phase: str) -> None:
    """Emit the provenance alert-parity signal when any anomaly exists. Always emits the loud
    greppable OBSERV WARNING (the in-log alert monitors key on); additionally publishes SNS when a
    topic is configured, mirroring referential_integrity._alert. Fail-safe: never raises.
    """
    try:
        if _provenance_anomaly_total(report) <= 0:
            return
        logger.warning(
            "OBSERV provenance.alert phase=%s missing=%d placeholders=%d structural_gaps=%s "
            "(provenance anomalies detected — data cannot be traced to its source)",
            phase or "?",
            int(report.get("total_missing_provenance", 0)),
            int(report.get("total_placeholder_records", 0)),
            ",".join(report.get("structural_gaps", [])) or "none",
        )
        _alert(report, phase)
    except (
        Exception
    ) as e:  # noqa: BLE001 - alerting is best-effort; never crash a phase
        logger.debug("provenance alert skipped (%s): %s", phase, e)


def _alert(report: Dict[str, Any], phase: str) -> None:
    """Best-effort SNS publish of a provenance anomaly, mirroring referential_integrity._alert
    (same NOTIFICATION_TOPIC_ARN + region resolution, aggregate counts only — no record values,
    PII-safe). No topic configured => skip quietly (the OBSERV WARNING already carries the signal).
    SNS->formatter->Slack is wired and live (verified 2026-10-30: 'DATA QUALITY — provenance anomalies'
    forwarded to Slack). Never raises.
    """
    import os

    topic = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
    if not topic:
        logger.debug("No NOTIFICATION_TOPIC_ARN — skipping provenance alert")
        return
    lines = [
        "DATA QUALITY: provenance-completeness anomalies detected (records that cannot be "
        "traced back to their source section/event).",
        f"Phase: {phase or '?'}",
        f"Missing-provenance records: {report.get('total_missing_provenance', 0)}",
        f"TODO-placeholder records: {report.get('total_placeholder_records', 0)}",
        f"Structural gaps (schema declares no source edge): "
        f"{','.join(report.get('structural_gaps', [])) or 'none'}",
    ]
    try:
        import boto3

        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        boto3.client("sns", region_name=region).publish(
            TopicArn=topic,
            Subject="WWII Pipeline: DATA QUALITY — provenance anomalies",
            Message="\n".join(lines),
        )
        logger.info("Sent provenance anomaly alert")
    except Exception as e:  # noqa: BLE001 - alerting is best-effort
        logger.warning("Failed to send provenance anomaly alert: %s", e)
