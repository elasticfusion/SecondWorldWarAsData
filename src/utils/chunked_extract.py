"""Shared utilities for chunked extraction with truncation recovery."""

import logging
from typing import Any, Callable, Dict, List, Optional, TypeVar

from src.grok_client import GrokTruncationError, BatchModeCollecting

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _replace(out: Dict[str, Any], new: Dict[str, Any]) -> None:
    out.update(new)


def extend_lists(out: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Merge for results keyed by sub-event whose values are LISTS: a sub-event split across
    several chunks (long text windows) accumulates every chunk's items instead of the last one
    replacing the others."""
    for key, items in (new or {}).items():
        if isinstance(items, list):
            out.setdefault(key, []).extend(items)  # type: ignore[union-attr]
        else:
            out[key] = items


def ask_all(items: List[Any], ask: Callable[[Any], None]) -> None:
    """``ask`` each item; a queued one (BatchModeCollecting) does not stop the rest - every request
    joins this batch round - and the signal is raised once all were asked."""
    collecting: Optional[BatchModeCollecting] = None
    for item in items:
        try:
            ask(item)
        except BatchModeCollecting as e:
            collecting = collecting or e
    if collecting is not None:
        raise collecting


def _extract_one_chunk_recursive(
    chunk: List[Any],
    extract_fn: Callable[[List[Any]], Dict[str, T]],
    entity_type: str,
    out: Dict[str, T],
    merge: Optional[Callable[[Dict[str, T], Dict[str, T]], None]] = None,
) -> None:
    """Extract one chunk, RECURSIVELY halving on truncation down to a single item.

    A half that STILL truncates splits again (the old code dropped it whole — losing ~half the
    items). Base case: a single item that still truncates is dropped alone (OBSERV), not a half-chunk.
    BatchModeCollecting is NEVER swallowed (the batch-COLLECT signal must propagate so the caller does
    not mark the event processed — the optional-entity-zero bug). Merges into ``out`` in place.
    """
    try:
        (merge or _replace)(out, extract_fn(chunk))
        return
    except BatchModeCollecting:
        raise  # collect pass: propagate, never swallow
    except GrokTruncationError:
        if len(chunk) <= 1:
            # Base case: a single item that still truncates. Drop ONLY this item (not a half-chunk)
            # so the rest of the batch is unaffected — visible, not silent.
            logger.error(
                "OBSERV chunk.item_dropped entity=%s reason=truncated_at_size_1 (item too large "
                "to extract even alone)",
                entity_type,
            )
            return
        mid = len(chunk) // 2
        logger.warning(
            "OBSERV chunk.split entity=%s size=%d -> %d+%d (truncation)",
            entity_type,
            len(chunk),
            mid,
            len(chunk) - mid,
        )
        # Recurse per half so a persistently-truncating half keeps halving (not dropped whole).
        # Both halves are asked even when the first is queued (batch mode), so they share a round.
        ask_all(
            [chunk[:mid], chunk[mid:]],
            lambda half: _extract_one_chunk_recursive(
                half, extract_fn, entity_type, out, merge
            ),
        )
    except Exception as e:  # noqa: BLE001
        # Non-truncation failure on this (sub)chunk: skip it gracefully so one bad chunk can't abort
        # the whole batch (preserves the prior leniency), but make it VISIBLE. NOT recursed (halving
        # only helps truncation) and NOT propagated (except BatchModeCollecting above).
        logger.error(
            "OBSERV chunk.skipped entity=%s size=%d err=%s", entity_type, len(chunk), e
        )


def extract_with_chunk_halving(
    chunks: List[List[Any]],
    extract_fn: Callable[[List[Any]], Dict[str, T]],
    entity_type: str,
    merge: Optional[Callable[[Dict[str, T], Dict[str, T]], None]] = None,
) -> Dict[str, T]:
    """Run extract_fn on each chunk. On truncation, RECURSIVELY halve and retry down to a single
    item (a too-large single item is dropped alone, with OBSERV). The batch-COLLECT signal
    (BatchModeCollecting) always propagates - AFTER every chunk was asked: stopping at the first
    queued chunk queued one chunk per batch round (chapter 8's weather took six rounds, one chunk
    each, 2026-11-13).

    Args:
        chunks: List of chunk lists (each chunk is a list of items to process)
        extract_fn: Function that takes a chunk and returns a dict of results
        entity_type: Name for logging (e.g., "casualties")

    Returns:
        Merged results dict from all chunks
    """
    all_results: Dict[str, T] = {}
    ask_all(
        chunks,
        lambda chunk: _extract_one_chunk_recursive(
            chunk, extract_fn, entity_type, all_results, merge
        ),
    )
    # Summary: items submitted vs results recovered, so net loss from truncation-drops / skipped
    # sub-chunks is visible (the per-branch OBSERV lines above explain any shortfall).
    items_in = sum(len(c) for c in chunks)
    logger.info(
        "OBSERV chunk.summary entity=%s chunks=%d items_in=%d results_out=%d",
        entity_type,
        len(chunks),
        items_in,
        len(all_results),
    )
    return all_results
