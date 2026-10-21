"""Shared utilities for chunked extraction with truncation recovery."""

import logging
from typing import Any, Callable, Dict, List, TypeVar

from src.grok_client import GrokTruncationError, BatchModeCollecting

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _extract_one_chunk_recursive(
    chunk: List[Any],
    extract_fn: Callable[[List[Any]], Dict[str, T]],
    entity_type: str,
    out: Dict[str, T],
) -> None:
    """Extract one chunk, RECURSIVELY halving on truncation down to a single item.

    A half that STILL truncates splits again (the old code dropped it whole — losing ~half the
    items). Base case: a single item that still truncates is dropped alone (OBSERV), not a half-chunk.
    BatchModeCollecting is NEVER swallowed (the batch-COLLECT signal must propagate so the caller does
    not mark the event processed — the optional-entity-zero bug). Merges into ``out`` in place.
    """
    try:
        out.update(extract_fn(chunk))
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
        _extract_one_chunk_recursive(chunk[:mid], extract_fn, entity_type, out)
        _extract_one_chunk_recursive(chunk[mid:], extract_fn, entity_type, out)
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
) -> Dict[str, T]:
    """Run extract_fn on each chunk. On truncation, RECURSIVELY halve and retry down to a single
    item (a too-large single item is dropped alone, with OBSERV). The batch-COLLECT signal
    (BatchModeCollecting) always propagates.

    Args:
        chunks: List of chunk lists (each chunk is a list of items to process)
        extract_fn: Function that takes a chunk and returns a dict of results
        entity_type: Name for logging (e.g., "casualties")

    Returns:
        Merged results dict from all chunks
    """
    all_results: Dict[str, T] = {}
    for chunk in chunks:
        _extract_one_chunk_recursive(chunk, extract_fn, entity_type, all_results)
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
