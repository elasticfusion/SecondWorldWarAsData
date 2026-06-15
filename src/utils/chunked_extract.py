"""Shared utilities for chunked extraction with truncation recovery."""

import logging
from typing import Any, Callable, Dict, List, TypeVar

from src.grok_client import GrokTruncationError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def extract_with_chunk_halving(
    chunks: List[List[Any]],
    extract_fn: Callable[[List[Any]], Dict[str, T]],
    entity_type: str,
) -> Dict[str, T]:
    """Run extract_fn on each chunk. On truncation, halve and retry.

    Args:
        chunks: List of chunk lists (each chunk is a list of items to process)
        extract_fn: Function that takes a chunk and returns a dict of results
        entity_type: Name for logging (e.g., "casualties")

    Returns:
        Merged results dict from all chunks
    """
    all_results: Dict[str, T] = {}

    for chunk in chunks:
        try:
            results = extract_fn(chunk)
        except GrokTruncationError:
            if len(chunk) > 1:
                logger.warning(
                    "  Truncation detected (%s) — splitting chunk of %d in half",
                    entity_type,
                    len(chunk),
                )
                mid = len(chunk) // 2
                for half in (chunk[:mid], chunk[mid:]):
                    try:
                        results = extract_fn(half)
                        all_results.update(results)
                    except Exception:
                        logger.error(
                            "  %s half-chunk also failed, skipping", entity_type
                        )
                continue
            raise
        all_results.update(results)

    return all_results
