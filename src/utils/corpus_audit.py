"""Corpus-level data-quality audit (block b-3) — READ-ONLY, reporting, never mutating.

Composes the EXISTING primitives (no reimplementation — avoids guard drift):
  - ``file_lock._validate_entity`` — the SAME schema guard the write path uses, run across the
    whole corpus to find records that are schema-invalid on disk (e.g. pre-guard fragments, or
    anything that slipped in via a raw-write bypass before the b-2 gate).
  - ``referential_integrity.audit`` — dangling cross-reference edges (every reference ID resolves).
  - a PK-uniqueness pass (two records sharing a primary key = an identity collision).

Read-only contract: records are validated on a DEEP COPY with ``record_stats=False`` so the guard's
in-place PK-repair/semantic-annotate and the stats catcher are never triggered against the live
corpus. Output is one report dict; a CLI wraps it with an optional fail-threshold for scheduled runs.
"""

import copy
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _schema_validity(storage: Any) -> Dict[str, Any]:
    """Run the SAME write-guard schema check across every entity record on disk (read-only).

    Whole-corpus: the flat PK entities, the schema-enforced entities without a flat PK
    (``map_features``), AND the nested event files (``content/<book>/*-event.json``)."""
    from src.utils import referential_integrity as ri
    from src.utils.file_lock import _validate_entity

    invalid: List[Dict[str, str]] = []
    total = 0

    def _check(entity_label: str, rel: str, data: Any) -> None:
        nonlocal total
        total += 1
        try:
            ok = _validate_entity(Path(rel), copy.deepcopy(data), record_stats=False)
        except Exception:  # noqa: BLE001 - audit must never crash on one record
            ok = True
        if not ok:
            invalid.append({"entity": entity_label, "key": rel})

    flat_dirs = set(ri.PK_FIELD.keys()) | {"map_features"}
    for entity_dir in flat_dirs:
        for rel, data in ri._iter_records(storage, ri._DIR.get(entity_dir, entity_dir)):
            _check(entity_dir, rel, data)
    for rel in ri._event_files(storage):
        try:
            data = storage.read_json(rel)
        except Exception:  # noqa: BLE001
            continue
        _check("events", rel, data)

    return {"records_checked": total, "invalid": invalid, "invalid_count": len(invalid)}


def _pk_uniqueness(storage: Any) -> Dict[str, Any]:
    """Detect primary-key collisions within an entity type (two records sharing one PK = an
    identity collision — the corpus must have at most one record per PK)."""
    from src.utils import referential_integrity as ri

    collisions: List[Dict[str, Any]] = []
    for entity, pk in ri.PK_FIELD.items():
        seen: Dict[str, str] = {}
        dupes: Dict[str, List[str]] = defaultdict(list)
        for rel, data in ri._iter_records(storage, ri._DIR.get(entity, entity)):
            v = data.get(pk)
            if not isinstance(v, str) or not v:
                continue
            if v in seen:
                if v not in dupes:
                    dupes[v].append(seen[v])
                dupes[v].append(rel)
            else:
                seen[v] = rel
        for pk_val, keys in dupes.items():
            collisions.append({"entity": entity, "pk": pk_val, "files": keys})
    return {"collisions": collisions, "collision_count": len(collisions)}


def audit_corpus(storage: Any) -> Dict[str, Any]:
    """Run all read-only corpus checks and return a single consolidated report."""
    from src.utils import referential_integrity as ri

    schema = _schema_validity(storage)
    referential = ri.audit(storage)
    uniqueness = _pk_uniqueness(storage)
    problems = (
        schema["invalid_count"]
        + referential["total_dangling"]
        + uniqueness["collision_count"]
    )
    return {
        "schema_validity": schema,
        "referential_integrity": referential,
        "pk_uniqueness": uniqueness,
        "total_problems": problems,
    }
