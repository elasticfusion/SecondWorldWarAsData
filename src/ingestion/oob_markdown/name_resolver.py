"""Resolve a raw name against an entity name-index — exact then fuzzy.

Entity-agnostic: the caller passes a name-index (``name -> entity_id``) built by
:func:`src.utils.entity_index.build_name_index` for *any* entity type, so this
same resolver serves people (``PersonID``) now and organizations (``GroupID``)
or equipment (``EquipmentID``) for OOB units later.

Two match tiers:

* **exact** — the query's normalized name equals an indexed normalized name.
  Auto-confirmed (no human review).
* **fuzzy** — high-similarity candidate, *gated by last-name agreement* so an
  OCR garble like "McLuliffe" does not resolve to "McAuliffe" while a real
  variant like "Doenitz"/"Dönitz" does. Flagged for human review (the existing
  dedup UI/exclusion flow is the place a reviewer confirms or rejects it).

The index built by ``build_name_index`` is keyed by *plain* ``name.lower()``.
This module re-keys it by :func:`src.utils.text_utils.normalize_name` so that
punctuation/accent differences (``"St. Vith"`` vs ``"st vith"``) do not cause a
true exact match to be missed — the bug the exact-only crosswalk had.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from src.utils.text_utils import normalize_name, similarity_ratio

# Similarity at/above this (with last-name agreement) is a fuzzy candidate.
FUZZY_THRESHOLD = 0.86
# Last names must be at least this similar to allow a fuzzy full-name match.
LAST_NAME_THRESHOLD = 0.82

MATCH_EXACT = "exact"
MATCH_FUZZY = "fuzzy"
MATCH_NONE = "none"

# Ranks/titles and generational suffixes stripped before taking the last token
# as the "last name". Lowercased, punctuation-free.
_TITLES = frozenset(
    {
        "gen",
        "general",
        "lt",
        "lieutenant",
        "col",
        "colonel",
        "maj",
        "major",
        "capt",
        "captain",
        "brig",
        "brigadier",
        "field",
        "marshal",
        "admiral",
        "commander",
        "sgt",
        "sergeant",
        "cpl",
        "corporal",
        "pvt",
        "private",
        "mr",
        "sir",
    }
)
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


@dataclass
class ResolveResult:
    """Outcome of resolving one name against an index."""

    entity_id: Optional[str] = None
    matched_name: Optional[str] = None
    method: str = MATCH_NONE
    confidence: float = 0.0

    @property
    def needs_review(self) -> bool:
        """Exact matches are trusted; fuzzy/none must be reviewed."""
        return self.method != MATCH_EXACT


@lru_cache(maxsize=5000)
def last_name(name: str) -> str:
    """Return the likely last name (rank/suffix-stripped, normalized)."""
    tokens = [t for t in normalize_name(name).split() if t]
    filtered = [t for t in tokens if t not in _TITLES and t not in _SUFFIXES]
    if filtered:
        return filtered[-1]
    return tokens[-1] if tokens else ""


class NameResolver:
    """Resolve names against a single entity name-index."""

    def __init__(self, name_index: Dict[str, str]):
        """Build a normalized lookup from a plain-lowercase name-index.

        Args:
            name_index: ``{name.lower() -> entity_id}`` from ``build_name_index``.
        """
        # Re-key by normalize_name so exact lookups are punctuation/accent-safe.
        # Keep the first-seen display form for reporting.
        self._by_norm: Dict[str, Tuple[str, str]] = {}
        for raw_name, entity_id in name_index.items():
            norm = normalize_name(raw_name)
            if norm and norm not in self._by_norm:
                self._by_norm[norm] = (entity_id, raw_name)
        # Precompute (norm, last_name, entity_id, raw) for fuzzy scanning.
        self._candidates: List[Tuple[str, str, str, str]] = [
            (norm, last_name(norm), eid, raw)
            for norm, (eid, raw) in self._by_norm.items()
        ]

    def __len__(self) -> int:
        return len(self._by_norm)

    def resolve(self, name: str) -> ResolveResult:
        """Resolve ``name`` to an entity id via exact then fuzzy matching."""
        if not name:
            return ResolveResult()
        norm = normalize_name(name)
        if not norm:
            return ResolveResult()

        # Tier 1: exact normalized match.
        exact = self._by_norm.get(norm)
        if exact:
            entity_id, raw = exact
            return ResolveResult(
                entity_id=entity_id,
                matched_name=raw,
                method=MATCH_EXACT,
                confidence=1.0,
            )

        # Tier 2: fuzzy, gated by last-name agreement.
        return self._best_fuzzy(norm)

    def _best_fuzzy(self, norm: str) -> ResolveResult:
        """Return the best last-name-gated fuzzy candidate, or a none-result."""
        query_last = last_name(norm)
        best: Optional[ResolveResult] = None
        for cand_norm, cand_last, entity_id, raw in self._candidates:
            # Require plausible last-name agreement to avoid OCR false merges.
            if query_last and cand_last:
                if similarity_ratio(query_last, cand_last) < LAST_NAME_THRESHOLD:
                    continue
            score = similarity_ratio(norm, cand_norm)
            if score < FUZZY_THRESHOLD:
                continue
            if best is None or score > best.confidence:
                best = ResolveResult(
                    entity_id=entity_id,
                    matched_name=raw,
                    method=MATCH_FUZZY,
                    confidence=round(score, 4),
                )
        return best or ResolveResult()
