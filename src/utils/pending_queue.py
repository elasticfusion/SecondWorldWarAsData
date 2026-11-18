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

Deferral (task 1.2b, owner 2026-11-18: "deferred yes, dropped never"). A row is never deleted
except on success. ``defer`` marks it with a cause:
  * ``funding`` / ``connectivity`` (AUTO_RESUME) - nothing wrong with the book: Grok credits, NAT,
    an AZ or the network. These are cluster-wide, so while one is recent NO book is runnable (no
    task is launched just to fail the same way); after ``queue.defer_retry_minutes`` the next
    reconcile tries again. The attempt is given back (``give_back``).
  * ``failure`` (past the attempt cap) / ``config`` (bad API key, unavailable model) - held until
    an operator runs ``scripts/resume_book.py`` (``resume``).
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

AUTO_RESUME = ("funding", "connectivity")
MANUAL_RESUME = ("failure", "config")
CAUSES = AUTO_RESUME + MANUAL_RESUME
DEFAULT_DEFER_RETRY_MINUTES = 50  # under the hourly reconcile, so its next run retries
RESUME_COMMAND = "python3 scripts/resume_book.py"


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


def _rows(table: Any, kind: str) -> List[dict]:
    """Every row of ``kind`` with its deferral fields (all scan pages)."""
    return list(
        scan_prefix(
            table,
            _prefix(kind),
            projection="#k, #d, #a, #r, #n",
            names={
                "#k": "cache_key",
                "#d": "defer_cause",
                "#a": "deferred_at",
                "#r": "defer_reason",
                "#n": "attempts",
            },
        )
    )


def _split_rows(table: Any, kind: str) -> Tuple[List[str], List[str], int, str]:
    """(runnable, held, newest auto-deferral time, its cause) over every row of ``kind``."""
    prefix = _prefix(kind)
    runnable, held, newest_auto, auto_cause = [], [], 0, ""
    for item in _rows(table, kind):
        book = str(item["cache_key"])[len(prefix) :]
        cause = item.get("defer_cause")
        at = int(item.get("deferred_at") or 0)
        if not book:
            continue
        if cause in MANUAL_RESUME:
            held.append(book)
            continue
        if cause in AUTO_RESUME and at > newest_auto:
            newest_auto, auto_cause = at, cause
        runnable.append(book)
    return sorted(runnable), sorted(held), newest_auto, auto_cause


def runnable_books(
    table: Any,
    kind: str,
    retry_minutes: int = DEFAULT_DEFER_RETRY_MINUTES,
    now: Optional[float] = None,
) -> List[str]:
    """Queued books that may be launched now, sorted. A ``failure``/``config`` deferral is held for
    the operator. A ``funding``/``connectivity`` deferral is cluster-wide: while the newest one is
    younger than ``retry_minutes`` nothing is runnable; after that every book is."""
    runnable, held, newest_auto, auto_cause = _split_rows(table, kind)
    if held:
        logger.info(
            "OBSERV queue.deferred_held kind=%s books=%s status=ok - resume each with `%s <book>`",
            kind,
            ",".join(held),
            RESUME_COMMAND,
        )
    now = time.time() if now is None else now
    wait = int(retry_minutes * 60 - (now - newest_auto)) if newest_auto else 0
    if wait > 0 and runnable:
        logger.info(
            "OBSERV queue.hold kind=%s cause=%s books=%d retry_in_s=%d status=ok - every book "
            "waits; the next reconcile after that retries",
            kind,
            auto_cause,
            len(runnable),
            wait,
        )
        return []
    return runnable


def deferred_rows(table: Any, kind: str = "enrich") -> List[dict]:
    """Deferred rows as {book, cause, reason, deferred_at, attempts}, sorted by book."""
    prefix = _prefix(kind)
    out = [
        {
            "book": str(i["cache_key"])[len(prefix) :],
            "cause": i["defer_cause"],
            "reason": i.get("defer_reason", ""),
            "deferred_at": int(i.get("deferred_at") or 0),
            "attempts": int(i.get("attempts") or 0),
        }
        for i in _rows(table, kind)
        if i.get("defer_cause")
    ]
    return sorted(out, key=lambda r: r["book"])


def next_book(
    table: Any,
    kind: str,
    exclude: str = "",
    retry_minutes: int = DEFAULT_DEFER_RETRY_MINUTES,
) -> str:
    """The first runnable book other than ``exclude`` (the running book), or ''."""
    for book in runnable_books(table, kind, retry_minutes):
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
    is created if absent, so a failed run is retried) and clear any deferral (this run is the
    retry). Returns (attempts, allowed). Past ``max_attempts`` the row is deferred ``failure``
    (kept, never dropped) and allowed is False: the caller must not run, and alerts.
    """
    key = {"cache_key": row_key("enrich", book)}
    resp = table.update_item(
        Key=key,
        UpdateExpression=(
            "SET book = :b, queued_at = if_not_exists(queued_at, :t), started_at = :t "
            "ADD attempts :one"
        ),
        ExpressionAttributeValues={":b": book, ":t": int(time.time()), ":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    attempts = int((resp.get("Attributes") or {}).get("attempts", 1))
    cap = max(1, int(max_attempts))
    if attempts > cap:
        try:
            defer(table, book, "failure", f"Phase 3 failed {cap} times")
        except (
            Exception
        ) as e:  # noqa: BLE001 - fail closed: past the cap the run never starts
            logger.error(
                "OBSERV queue.defer_failed book=%s err=%s status=error - past the cap, not run; "
                "the row keeps its count, so the next start is refused again",
                book,
                type(e).__name__,
            )
        return attempts, False
    # only now clear a deferral (this run is the retry): clearing it in the counting update
    # would make a capped row look runnable until the re-defer above
    try:
        table.update_item(
            Key=key,
            UpdateExpression="REMOVE defer_cause, deferred_at, defer_reason",
            ConditionExpression="attribute_exists(cache_key)",
        )
    except Exception as e:  # noqa: BLE001 - the attempt IS counted; run it
        logger.error(
            "OBSERV queue.defer_clear_failed book=%s err=%s status=error - the attempt is "
            "counted, but an old deferral may remain on the row; `%s %s` clears it",
            book,
            type(e).__name__,
            RESUME_COMMAND,
            book,
        )
    return attempts, True


def defer(table: Any, book: str, cause: str, reason: str = "") -> bool:
    """Mark ``book``'s existing Phase 3 row deferred for ``cause``: kept, never dropped. Returns
    False if the book has no row (it finished, or was never queued): a finished book is never
    re-queued. ``reason`` is stored one-line and capped (fixed strings today)."""
    if cause not in CAUSES:
        raise ValueError(f"unknown deferral cause {cause!r}")
    try:
        table.update_item(
            Key={"cache_key": row_key("enrich", book)},
            UpdateExpression="SET defer_cause = :c, deferred_at = :t, defer_reason = :r",
            ConditionExpression="attribute_exists(cache_key)",
            ExpressionAttributeValues={
                ":t": int(time.time()),
                ":c": cause,
                ":r": " ".join(str(reason).split())[:500],
            },
        )
        return True
    except Exception as e:  # noqa: BLE001 - re-raised unless it is the guard
        if _condition_failed(e):
            return False
        raise


def _condition_failed(e: Exception) -> bool:
    code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code", "")
    return code == "ConditionalCheckFailedException"


def give_back(table: Any, book: str) -> bool:
    """Undo this task's attempt (the stop was not the book's fault). Only on an existing row
    with attempts > 0: a finished book's row is never re-created. Returns True if given back.
    """
    try:
        table.update_item(
            Key={"cache_key": row_key("enrich", book)},
            UpdateExpression="ADD attempts :minus",
            ConditionExpression="attribute_exists(cache_key) AND attempts > :zero",
            ExpressionAttributeValues={":minus": -1, ":zero": 0},
        )
        return True
    except Exception as e:  # noqa: BLE001 - re-raised unless it is the guard
        if _condition_failed(e):
            return False
        raise


def resume(table: Any, book: str) -> bool:
    """Operator resume: clear the deferral and the attempt count on an existing row, so the next
    reconcile runs the book. Returns False if the book has no row."""
    try:
        table.update_item(
            Key={"cache_key": row_key("enrich", book)},
            UpdateExpression=(
                "SET attempts = :zero REMOVE defer_cause, deferred_at, defer_reason"
            ),
            ConditionExpression="attribute_exists(cache_key)",
            ExpressionAttributeValues={":zero": 0},
        )
        return True
    except Exception as e:  # noqa: BLE001 - re-raised unless it is the guard
        if _condition_failed(e):
            return False
        raise


def defer_retry_minutes(config: Optional[dict] = None) -> int:
    """``queue.defer_retry_minutes`` (default 50)."""
    try:
        value = int(((config or {}).get("queue") or {}).get("defer_retry_minutes", 0))
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_DEFER_RETRY_MINUTES


def max_attempts(config: Optional[dict] = None) -> int:
    """``queue.phase3_max_attempts`` (default 3)."""
    try:
        value = int(((config or {}).get("queue") or {}).get("phase3_max_attempts", 0))
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_MAX_ATTEMPTS
