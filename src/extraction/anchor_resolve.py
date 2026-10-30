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
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Minimum normalized-overlap ratio for the fuzzy fallback to accept a match (below -> unresolved).
_FUZZY_THRESHOLD = 0.85
# A quote shorter than this (normalized chars) is too generic to anchor reliably -> skip fuzzy.
_MIN_ANCHOR_LEN = 12


def _normalize(text: str) -> str:
    """Collapse whitespace, casefold, and reduce punctuation runs so minor model reformatting of a
    verbatim quote still matches the source paragraph. Deterministic + pure."""
    t = (text or "").casefold()
    t = re.sub(r"[^\w\s]", " ", t)  # punctuation -> space (keeps word boundaries)
    t = " ".join(t.split())  # collapse all whitespace
    return t


def _build_index(paragraphs: List[Dict[str, Any]]) -> List[Tuple[int, str, str]]:
    """(absolute_number, raw_text, normalized_text) for each parsed paragraph. Fail-safe: skips
    malformed entries."""
    index: List[Tuple[int, str, str]] = []
    for p in paragraphs or []:
        if not isinstance(p, dict):
            continue
        num = p.get("absolute_number")
        try:
            num = int(num)
        except (TypeError, ValueError):
            continue
        raw = p.get("text", "") or ""
        index.append((num, raw, _normalize(raw)))
    return index


def _match_quote(
    norm_quote: str, index: List[Tuple[int, str, str]]
) -> Tuple[int | None, str]:
    """Resolve one normalized quote to a paragraph absolute_number.

    Returns (absolute_number | None, method): "exact" (normalized substring), "fuzzy" (best
    SequenceMatcher ratio >= threshold), or "" when unresolved. Never guesses: ambiguous/low
    confidence -> None.
    """
    if not norm_quote:
        return None, ""
    # 1) exact normalized-substring match (first paragraph that contains the quote)
    for num, _raw, norm in index:
        if norm_quote in norm:
            return num, "exact"
    # 2) fuzzy fallback — only for anchors long enough to be discriminating
    if len(norm_quote) < _MIN_ANCHOR_LEN:
        return None, ""
    best_num, best_ratio = None, 0.0
    for num, _raw, norm in index:
        # compare the quote against the most similar window of the paragraph (quick ratio first)
        ratio = SequenceMatcher(None, norm_quote, norm).quick_ratio()
        if ratio > best_ratio:
            best_ratio, best_num = SequenceMatcher(None, norm_quote, norm).ratio(), num
    if best_num is not None and best_ratio >= _FUZZY_THRESHOLD:
        return best_num, "fuzzy"
    return None, ""


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
        index = _build_index(paragraphs)
        resolved: set[int] = set()
        unresolved: List[str] = []
        for q in anchor_quotes or []:
            num, _method = _match_quote(_normalize(q), index)
            (unresolved.append(q) if num is None else resolved.add(num))
        _observe_unresolved(sub_event_id, unresolved, len(anchor_quotes or []))
        return sorted(resolved), unresolved
    except Exception as e:  # noqa: BLE001 - resolver must never crash extraction
        logger.warning("anchor resolution failed for %s: %s", sub_event_id, e)
        return [], list(anchor_quotes or [])
