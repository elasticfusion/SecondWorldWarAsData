"""The per-book phase queues in the api-cache table: ``pending#parsed#<book>`` (waiting for
Phase 2) and ``pending#enrich#<book>`` (waiting for Phase 3). Task 1.2, 2026-11-18.

ONE implementation for the trigger Lambda and the ECS wrapper (both read and write these rows).

Phase 3 row lifecycle (owner decisions D8c-2, D10; finding V3):
  * queued   - the trigger found the Phase 3 lock held: ``queue_enrich`` writes the row;
  * started  - the task counts the attempt FIRST (``claim_attempt``, creating the row if the book
               was launched without one). Counting at the start bounds EVERY way a run can end -
               a crash, a preflight abort, a SIGTERM, a gate failure - with no exit-path hooks;
               past ``max_attempts`` the task does not run, removes the row and alerts;
  * success  - the task deletes ITS OWN row (``finish``);
  * anything else - the row stays, so the hourly reconcile retries the book.
Before: the row was deleted when the NEXT book was triggered, so a failed book was never retried
and a book launched by the reconcile could be picked as its own "next book" and run twice.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, Tuple

from src.utils.ddb_scan import scan_prefix

logger = logging.getLogger(__name__)

KINDS = ("parsed", "enrich")
DEFAULT_MAX_ATTEMPTS = 3


def _prefix(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown queue kind {kind!r}")
    return f"pending#{kind}#"


def row_key(kind: str, book: str) -> str:
    """``pending#<kind>#<book>``. The book must be a safe slug (no '#', so it cannot address
    another row family; src/ingestion/book_slug.py)."""
    if not book or "#" in book or book.strip() != book:
        raise ValueError(f"not a safe book name for a queue row: {book!r}")
    return _prefix(kind) + book


def pending_books(table: Any, kind: str) -> List[str]:
    """Every book queued for ``kind``, sorted (all scan pages)."""
    prefix = _prefix(kind)
    books = {
        str(item["cache_key"])[len(prefix) :]
        for item in scan_prefix(table, prefix, projection="cache_key")
    }
    return sorted(b for b in books if b)


def next_book(table: Any, kind: str, exclude: str = "") -> str:
    """The first queued book other than ``exclude`` (the running book), or ''."""
    for book in pending_books(table, kind):
        if book != exclude:
            return book
    return ""


def queue_enrich(table: Any, book: str) -> None:
    """Queue ``book`` for Phase 3. Idempotent; keeps an existing attempt count."""
    table.update_item(
        Key={"cache_key": row_key("enrich", book)},
        UpdateExpression="SET book = :b, queued_at = if_not_exists(queued_at, :t)",
        ExpressionAttributeValues={":b": book, ":t": int(time.time())},
    )


def finish(table: Any, kind: str, book: str) -> None:
    """The book's phase succeeded: remove its own row (absent is fine)."""
    table.delete_item(Key={"cache_key": row_key(kind, book)})


def claim_attempt(
    table: Any, book: str, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> Tuple[int, bool]:
    """A Phase 3 task for ``book`` is starting: count the attempt on its row (atomic ADD; the row
    is created if absent, so a failed run is retried). Returns (attempts, allowed). Past
    ``max_attempts`` the row is removed and allowed is False: the caller must not run, and alerts.
    """
    resp = table.update_item(
        Key={"cache_key": row_key("enrich", book)},
        UpdateExpression=(
            "SET book = :b, queued_at = if_not_exists(queued_at, :t), started_at = :t "
            "ADD attempts :one"
        ),
        ExpressionAttributeValues={":b": book, ":t": int(time.time()), ":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    attempts = int((resp.get("Attributes") or {}).get("attempts", 1))
    if attempts > max(1, int(max_attempts)):
        finish(table, "enrich", book)
        return attempts, False
    return attempts, True


def max_attempts(config: Optional[dict] = None) -> int:
    """``queue.phase3_max_attempts`` (default 3)."""
    try:
        value = int(((config or {}).get("queue") or {}).get("phase3_max_attempts", 0))
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_MAX_ATTEMPTS
