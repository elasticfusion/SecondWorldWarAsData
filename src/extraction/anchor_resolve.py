"""Deterministic anchor-quote -> paragraph resolution (PROTOTYPE, 2026-10-30).

The real fix for the paragraph_numbers starvation class: the LLM returns short VERBATIM quotes per
sub-event (comprehension), and THIS code deterministically maps each quote to the paragraph
``absolute_number`` whose text contains it (identity). The LLM never owns a provenance key — mirroring
the server-minted PersonID/GroupID rule.

A quote is self-validating: it either appears in the source text or it does not. A hallucinated quote
resolves to nothing and is reported loudly (``OBSERV events.anchor_unresolved``) rather than silently
pointing at a wrong-but-valid paragraph number (the failure mode of LLM-transcribed numbers).

See docs/current/design/deterministic-paragraph-resolution.md.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from src.utils.quote_match import build_index, find_quote, normalize

logger = logging.getLogger(__name__)


def _observe_unresolved(sub_event_id: str, unresolved: List[str], total: int) -> None:
    """Loud OBSERV for anchor quotes that didn't resolve (hallucinated/paraphrased). Fail-safe."""
    if not unresolved:
        return
    try:
        logger.warning(
            "OBSERV events.anchor_unresolved sub_event=%s unresolved=%d/%d samples=%s "
            "(anchor quote(s) not found in source — hallucinated/paraphrased; NOT fabricating "
            "a paragraph number)",
            sub_event_id,
            len(unresolved),
            total,
            [u[:50] for u in unresolved[:3]],
        )
    except Exception:  # noqa: BLE001 - observability must never raise
        pass


def resolve_anchor_paragraphs(
    anchor_quotes: List[str], paragraphs: List[Dict[str, Any]], sub_event_id: str = "?"
) -> Tuple[List[int], List[str]]:
    """Map a sub-event's anchor quotes to paragraph absolute_numbers, deterministically.

    Returns (resolved_numbers_sorted_unique, unresolved_quotes). Emits a loud OBSERV for any
    unresolved quote (a hallucinated/paraphrased anchor) — NEVER fabricates a number. Fail-safe:
    bookkeeping errors return ([], all-quotes-unresolved) rather than raising.
    """
    try:
        index = build_index(paragraphs)
        resolved: set[int] = set()
        unresolved: List[str] = []
        for q in anchor_quotes or []:
            num, _method = find_quote(normalize(q), index)
            (unresolved.append(q) if num is None else resolved.add(num))
        _observe_unresolved(sub_event_id, unresolved, len(anchor_quotes or []))
        return sorted(resolved), unresolved
    except Exception as e:  # noqa: BLE001 - resolver must never crash extraction
        logger.warning("anchor resolution failed for %s: %s", sub_event_id, e)
        return [], list(anchor_quotes or [])
