"""Validation statistics catcher — data-quality telemetry for the write guard.

Every write-guard outcome (allow / block / allow-with-warning) is recorded here as a structured
datapoint, clustered by (entity, outcome, validator-keyword, version-state). At the end of a run
`write_validation_stats()` dumps the aggregate to output/metrics/validation_stats.json and surfaces
SYSTEMATIC clusters — a block rate for an (entity, keyword) pair above a threshold is the signal
that the extraction/dedup/schema CODE is wrong (reprocessing can't fix a systematic defect), vs.
isolated/transient failures that an automated reprocess would clear.

This is the dependency-free first piece of the corrective loop: it needs no reprocessing or S3
infra, and it is useful immediately — it is the telemetry the pipeline was missing (e.g. it would
have flagged the ~35%-invalid merge-fragment problem automatically).

Thread-safe (merges run multi-threaded). Fail-safe: recording never raises into the guard.
"""

import logging
import threading
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Outcomes recorded for every guarded write.
OUTCOME_ALLOW = "allow"  # validated OK (or non-entity passthrough)
OUTCOME_BLOCK = "block"  # schema-invalid -> write refused
OUTCOME_WARN = (
    "allow_warn"  # legitimately-old record allowed despite a current-schema miss
)
OUTCOME_EMPTY = (
    "allow_empty"  # wrote, but populated-field count below the entity's floor
)
OUTCOME_FLAGGED = (
    "allow_flagged"  # wrote, but carries semantic quality_flags (one per code)
)

# Outcomes that signal a possible CODE defect when they CLUSTER (not just block anymore): a book-
# wide run of empty records or the same semantic flag is as diagnostic as a block cluster.
_CONCERN_OUTCOMES = (OUTCOME_BLOCK, OUTCOME_EMPTY, OUTCOME_FLAGGED)

# A cluster is "systematic" (code-level, not transient) when both hold:
#   its concern count >= _CLUSTER_MIN_COUNT  AND  its RATE for that entity >= _CLUSTER_MIN_RATE
_CLUSTER_MIN_COUNT = 10
_CLUSTER_MIN_RATE = (
    0.05  # 5% of an entity's writes blocked on the same keyword = systematic
)

# Minimum count of POPULATED content fields a real record of each entity should carry (below =>
# allow_empty). Only entities listed here are checked; others never emit allow_empty. Conservative
# on purpose — this flags the egregiously-empty (``{}`` biographies, bare PK-only records), not the
# merely-sparse. Metadata/PK/quality_flags are never counted as content.
_EMPTY_FLOORS = {
    "people": 2,
    "places": 2,
    "bibliography": 2,
    "equipment": 2,
    "dates": 2,
}
_NONCONTENT_FIELDS = {
    "_schema_version",
    "_last_updated",
    "quality_flags",
    "enrichment_status",
    "last_enrichment_search",
    # Recheck-tracking (RESULT_DEDUP_DESIGN.md #7): "we looked again" is metadata, not
    # content. These change on every 90-day recheck even when the actual facts don't, so
    # they MUST be excluded from change-detection (the future content hash) or a pure
    # recheck would needlessly re-publish/re-embed the entity. (checked_at/history live
    # on result-list ENTRIES; listed here as the convention anchor so the loader's
    # content hash excludes them wherever they appear.)
    "checked_at",
    "history",
}


def _known_flag_codes() -> frozenset:
    """The semantic-flag vocabulary we will record (bounds the allow_flagged keyword to static,
    PII-safe codes). Fail-safe: empty set if the module is unavailable."""
    try:
        from src.utils.semantic_flags import _OWNED_FLAGS

        return frozenset(_OWNED_FLAGS)
    except Exception:  # noqa: BLE001
        return frozenset()


_KNOWN_FLAG_CODES = _known_flag_codes()
_PK_MAP_CACHE: Optional[Dict[str, str]] = None


def _entity_pk_map() -> Dict[str, str]:
    """Cached entity -> primary-key-field map from the registry (hot path: one lookup per write)."""
    global _PK_MAP_CACHE
    if _PK_MAP_CACHE is None:
        try:
            from src.schemas.entity_registry import ENTITY_REGISTRY

            _PK_MAP_CACHE = {
                s.name: s.required_id for s in ENTITY_REGISTRY if s.required_id
            }
        except Exception:  # noqa: BLE001
            _PK_MAP_CACHE = {}
    return _PK_MAP_CACHE


def _is_populated(key: str, value: Any) -> bool:
    """A field carries information unless it is null/empty-string/empty-list/empty-dict, or the
    0.0/0.0 coordinate placeholder (lat/lon==0.0 is the 'no geocode' sentinel, not real data).
    """
    if value is None or value == "" or value == [] or value == {}:
        return False
    if (
        key in ("latitude", "longitude")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value) == 0.0
    ):
        return False
    return True


def _populated_content_count(entity: str, record: Dict[str, Any]) -> int:
    """Count populated CONTENT fields (excludes metadata, the PK, and quality_flags)."""
    pk = _entity_pk_map().get(entity)
    skip = _NONCONTENT_FIELDS | ({pk} if pk else set())
    return sum(1 for k, v in record.items() if k not in skip and _is_populated(k, v))


_lock = threading.Lock()
# key: (entity, outcome, keyword, version_state) -> count
_counts: "Counter[tuple]" = Counter()


def reset_stats() -> None:
    with _lock:
        _counts.clear()


def record_validation(
    entity: str,
    outcome: str,
    keyword: Optional[str] = None,
    version_state: str = "ok",
) -> None:
    """Record one guard outcome. keyword = the violated jsonschema validator (e.g. 'required',
    'pattern') for blocks/warns, None for allows. Never raises."""
    try:
        with _lock:
            _counts[(entity, outcome, keyword or "-", version_state)] += 1
    except Exception:  # noqa: BLE001 - telemetry must never break the guard
        pass


def record_record_quality(
    entity: str, record: Dict[str, Any], version_state: str = "ok"
) -> None:
    """For an ALLOWED record, record low-information + semantic-flag observations so the catcher
    can surface SYSTEMATIC content-quality clusters that a plain ``allow`` hides. Never raises.

    - ``allow_empty`` (keyword ``below_floor``) when the entity has a floor and the record's
      populated content-field count is below it.
    - ``allow_flagged`` once per ``quality_flags`` code (keyword = the code)."""
    try:
        if not isinstance(record, dict):
            return
        floor = _EMPTY_FLOORS.get(entity)
        if floor is not None and _populated_content_count(entity, record) < floor:
            record_validation(entity, OUTCOME_EMPTY, "below_floor", version_state)
        flags = record.get("quality_flags") or []
        if isinstance(flags, list):
            for code in flags:
                # Only record codes from the KNOWN vocabulary — a flag keyword flows into logs/SNS,
                # so never let a future/foreign producer put record-derived text into it (PII-safe).
                if isinstance(code, str) and code in _KNOWN_FLAG_CODES:
                    record_validation(entity, OUTCOME_FLAGGED, code, version_state)
    except Exception:  # noqa: BLE001 - telemetry must never break the guard
        pass


def _entity_totals() -> Dict[str, int]:
    totals: "Counter[str]" = Counter()
    with _lock:
        items = list(_counts.items())
    for (entity, _o, _k, _v), n in items:
        totals[entity] += n
    return dict(totals)


def systematic_clusters() -> List[Dict[str, Any]]:
    """Return CONCERN clusters that look like a CODE defect (count + rate thresholds), sorted by
    severity. Concern outcomes = block + allow_empty + allow_flagged (a book-wide run of empty or
    same-flag records is as diagnostic as a block cluster). Each row:
    {entity, outcome, keyword, version_state, count, entity_total, rate}."""
    totals = _entity_totals()
    out: List[Dict[str, Any]] = []
    with _lock:
        items = list(_counts.items())
    for (entity, outcome, keyword, version_state), n in items:
        if outcome not in _CONCERN_OUTCOMES:
            continue
        total = totals.get(entity, 0) or 1
        rate = n / total
        if n >= _CLUSTER_MIN_COUNT and rate >= _CLUSTER_MIN_RATE:
            out.append(
                {
                    "entity": entity,
                    "outcome": outcome,
                    "keyword": keyword,
                    "version_state": version_state,
                    "count": n,
                    "blocks": n,  # back-compat alias for existing consumers
                    "entity_total": total,
                    "rate": round(rate, 4),
                }
            )
    # Sort by severity (rate, then count) descending; entity/keyword ascending as a stable
    # tiebreaker so the JSON/alert order is deterministic across runs.
    out.sort(key=lambda c: (c["entity"], c["keyword"]))
    out.sort(key=lambda c: (c["rate"], c["blocks"]), reverse=True)
    return out


def get_stats() -> Dict[str, Any]:
    """Snapshot: per-outcome totals, per-entity breakdown, and systematic clusters."""
    with _lock:
        items = list(_counts.items())
    by_outcome: "Counter[str]" = Counter()
    by_entity: Dict[str, Counter] = {}
    for (entity, outcome, keyword, version_state), n in items:
        by_outcome[outcome] += n
        by_entity.setdefault(entity, Counter())[(outcome, keyword, version_state)] += n
    detail = {
        e: [
            {"outcome": o, "keyword": k, "version_state": v, "count": c}
            for (o, k, v), c in sorted(cc.items(), key=lambda x: -x[1])
        ]
        for e, cc in by_entity.items()
    }
    total = sum(by_outcome.values()) or 1
    return {
        "totals": dict(by_outcome),
        "block_rate": round(by_outcome.get(OUTCOME_BLOCK, 0) / total, 4),
        "by_entity": detail,
        "systematic_clusters": systematic_clusters(),
    }


def write_validation_stats(output_dir: Any) -> Dict[str, Any]:
    """Dump the aggregate to output/metrics/validation_stats.json + log a one-line summary and,
    if any systematic cluster exists, a loud warning (the upstream-fix signal). Fail-safe.
    """
    import json as _json
    from datetime import datetime, timezone
    from pathlib import Path

    snap = get_stats()
    snap["generated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        mdir = Path(output_dir) / "metrics"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "validation_stats.json").write_text(
            _json.dumps(snap, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not write validation stats: %s", e)

    t = snap["totals"]
    logger.info(
        "Validation stats: allow=%d block=%d allow_warn=%d allow_empty=%d allow_flagged=%d "
        "(block_rate=%.1f%%)",
        t.get(OUTCOME_ALLOW, 0),
        t.get(OUTCOME_BLOCK, 0),
        t.get(OUTCOME_WARN, 0),
        t.get(OUTCOME_EMPTY, 0),
        t.get(OUTCOME_FLAGGED, 0),
        snap["block_rate"] * 100,
    )
    for c in snap["systematic_clusters"]:
        logger.warning(
            "SYSTEMATIC %s cluster (likely a CODE defect — reprocess cannot fix): "
            "%s / '%s' %d/%d (%.1f%%) — fix upstream",
            c.get("outcome", OUTCOME_BLOCK),
            c["entity"],
            c["keyword"],
            c.get("count", c.get("blocks", 0)),
            c["entity_total"],
            c["rate"] * 100,
        )
    # Systematic clusters are an UPSTREAM-CODE-BUG signal — alert the operator, don't just log.
    # Publish free-form text to the {env}-wwii-phase2-complete SNS topic, which fans out to
    # email (now) + the Slack formatter (later) per the project alerting convention.
    if snap["systematic_clusters"]:
        _alert_systematic(snap["systematic_clusters"], snap["block_rate"])
    return snap


def _alert_systematic(clusters: List[Dict[str, Any]], block_rate: float) -> None:
    """Email/Slack a data-quality bug alert for systematic validation-failure clusters.
    Fail-safe: never raise into the caller. No record values are included (PII-safe)."""
    import os

    topic = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
    if not topic:
        logger.debug("No NOTIFICATION_TOPIC_ARN — skipping systematic-failure alert")
        return
    lines = [
        "DATA QUALITY: systematic validation failures detected (likely an upstream CODE "
        "defect — targeted reprocess cannot fix these).",
        f"Overall write block rate: {block_rate * 100:.1f}%",
        "",
        "Clusters (entity / outcome / detail / count / rate):",
    ]
    for c in clusters:
        lines.append(
            f"  - {c['entity']}: {c.get('outcome', OUTCOME_BLOCK)} '{c['keyword']}' "
            f"{c.get('count', c.get('blocks', 0))}/{c['entity_total']} ({c['rate'] * 100:.1f}%)"
        )
    lines.append("")
    lines.append(
        "Action: fix the extraction/dedup/schema code for the above entity(ies); "
        "see output/metrics/validation_stats.json for the full breakdown."
    )
    try:
        import boto3

        region = os.environ.get(
            "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        )
        boto3.client("sns", region_name=region).publish(
            TopicArn=topic,
            Subject="WWII Pipeline: DATA QUALITY — systematic validation failures",
            Message="\n".join(lines),
        )
        logger.info(
            "Sent systematic-failure data-quality alert (%d cluster(s))", len(clusters)
        )
    except Exception as e:  # noqa: BLE001 - alerting is best-effort
        logger.warning("Failed to send data-quality alert: %s", e)
