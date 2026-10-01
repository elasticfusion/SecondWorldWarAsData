"""Incremental continuous dedup via blocking (M6 / CONCURRENCY_AND_NAT_SPEC §3.2/§11).

Replaces the all-pairs O(n^2) dedup barrier with an ongoing, sublinear process:
as each book finishes Phase 2, dedup ITS entities against the running resolved
set, then flow to Phase 3. No wave, no quiescence, no straggler.

Sublinearity comes from BLOCKING: each new entity is compared only against a
small CANDIDATE set sharing a blocking key (normalized last-name / geo cell),
not the whole corpus. Confident matches auto-merge (idempotent, via M5
merge_entity); ambiguous pairs go to a rolling review queue.

This module owns the mechanism (index + candidate lookup + auto/review split);
the pairwise match SCORE reuses the existing heuristics in
scripts.find_duplicate_people so behavior matches the batch path.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Auto-merge at/above this similarity; review the band below it; ignore lower.
AUTO_MERGE_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.75


def _norm_key(name: str) -> str:
    """Blocking key for a name — normalized last token, first 4 chars.

    Cheap and collision-tolerant: groups likely-same entities into the same
    bucket so we compare small candidate sets. Reuses the project's unicode/last
    -name normalization for consistency with the batch matcher.
    """
    try:
        from scripts.find_duplicate_people import (
            _extract_last_name,
            _normalize_unicode,
        )

        last = _extract_last_name(_normalize_unicode(name or ""))
    except Exception:  # pragma: no cover - fallback if import shape changes
        last = (name or "").split()[-1] if name else ""
    return last.lower()[:4]


def _geo_cell(entity: Dict[str, Any], precision: int = 1) -> Optional[str]:
    """Blocking key for a place: coarse lat/lon cell. None if no usable coords."""
    lat = entity.get("latitude")
    lon = entity.get("longitude")
    try:
        if lat in (None, 0, 0.0) and lon in (None, 0, 0.0):
            return None
        return f"{round(float(lat), precision)},{round(float(lon), precision)}"
    except (TypeError, ValueError):
        return None


def blocking_keys(entity: Dict[str, Any], entity_type: str) -> List[str]:
    """Return the blocking keys under which an entity is indexed / looked up."""
    keys: List[str] = []
    name = (
        entity.get("name")
        or entity.get("current_name")
        or entity.get("common_name")
        or entity.get("group_name")
        or ""
    )
    if name:
        keys.append(f"name:{_norm_key(name)}")
    if entity_type == "places":
        cell = _geo_cell(entity)
        if cell:
            keys.append(f"geo:{cell}")
    return keys


class ResolvedSetIndex:
    """In-memory blocking index over the running resolved set for one entity type.

    Maps blocking_key -> list of (entity_id, entity_data). Built once from the
    store's current entities, then each new entity does a small candidate lookup.
    Kept in-memory per dedup pass (bounded = that book's entities x candidates).
    """

    def __init__(self, entity_type: str):
        self.entity_type = entity_type
        self._buckets: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}

    def add(self, entity_id: str, data: Dict[str, Any]) -> None:
        for k in blocking_keys(data, self.entity_type):
            self._buckets.setdefault(k, []).append((entity_id, data))

    def candidates(self, data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
        """Candidate (id, data) pairs sharing any blocking key with `data`."""
        seen: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        for k in blocking_keys(data, self.entity_type):
            for eid, edata in self._buckets.get(k, []):
                seen[eid] = (eid, edata)
        return list(seen.values())

    @classmethod
    def from_entities(
        cls, entity_type: str, entities: List[Tuple[str, Dict[str, Any]]]
    ) -> "ResolvedSetIndex":
        idx = cls(entity_type)
        for eid, data in entities:
            idx.add(eid, data)
        return idx


def _score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Similarity score for two entities, reusing the batch matcher's ratio."""
    from scripts.find_duplicate_people import _similarity_ratio

    def _name(e: Dict[str, Any]) -> str:
        return (
            e.get("name")
            or e.get("current_name")
            or e.get("common_name")
            or e.get("group_name")
            or ""
        )

    return _similarity_ratio(_name(a), _name(b))


def _best_candidate(
    new_id: str, data: Dict[str, Any], index: "ResolvedSetIndex"
) -> Tuple[Optional[str], float]:
    """Return (best_existing_id, score) among blocking candidates for `data`."""
    best_id: Optional[str] = None
    best_score = 0.0
    for cand_id, cand_data in index.candidates(data):
        if cand_id == new_id:
            continue
        s = _score(data, cand_data)
        if s > best_score:
            best_score, best_id = s, cand_id
    return best_id, best_score


def dedup_incremental(
    entity_type: str,
    new_entities: List[Tuple[str, Dict[str, Any]]],
    index: ResolvedSetIndex,
    *,
    merge_fn: Optional[Callable[[str, str, Dict[str, Any]], bool]] = None,
    review_fn: Optional[Callable[[str, str, float], None]] = None,
) -> Dict[str, int]:
    """Dedup new entities against the resolved-set `index` (sublinear via blocking).

    For each new entity: find candidates (small set), score against each, and:
      - >= AUTO_MERGE_THRESHOLD  -> auto-merge into the existing entity (idempotent)
      - [REVIEW, AUTO)           -> enqueue for rolling human review (no barrier)
      - < REVIEW                 -> treat as new; add to the index so later entities
                                    in THIS pass can match it.
    merge_fn(existing_id, new_id, new_data)->bool and review_fn(existing_id, new_id,
    score) are injected (defaults: log-only) so the caller wires M5 merge_entity /
    the review queue. Returns counts {auto_merged, review, new}.
    """
    counts = {"auto_merged": 0, "review": 0, "new": 0}
    for new_id, data in new_entities:
        best_id, best_score = _best_candidate(new_id, data, index)
        if best_id and best_score >= AUTO_MERGE_THRESHOLD:
            if merge_fn:
                merge_fn(best_id, new_id, data)
            counts["auto_merged"] += 1
            logger.info("dedup auto-merge %s -> %s (%.2f)", new_id, best_id, best_score)
        elif best_id and best_score >= REVIEW_THRESHOLD:
            if review_fn:
                review_fn(best_id, new_id, best_score)
            counts["review"] += 1
            # Do NOT add to index — it may merge on review; keep it out of auto.
        else:
            index.add(new_id, data)  # genuinely new — later entities can match it
            counts["new"] += 1
    logger.info("dedup_incremental (%s): %s", entity_type, counts)
    return counts
