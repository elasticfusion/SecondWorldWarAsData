#!/usr/bin/env python3
"""List and resume deferred Phase 3 books (task 1.2b, owner 2026-11-18: "deferred yes, dropped
never"). A book is deferred instead of dropped: ``failure`` past the attempt cap, ``config`` for a
bad API key or model (both held until resumed here), or ``funding`` / ``connectivity`` (resumed by
the reconcile on its own; resuming one here only ends the hold sooner).

  list                 every deferred book: cause, reason, attempts, when
  <book> [<book> ...]  clear the deferral and the attempt count; the next hourly reconcile runs it
  --all [--cause C]    resume every deferred book (optionally only cause C)

Uses the api-cache table named by CACHE_TABLE (default dev-wwii-api-cache) in AWS_DEFAULT_REGION.
Exit 1 if a named book has no queue row or an AWS call fails.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils import pending_queue  # noqa: E402


def _table() -> Any:
    import boto3

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
    return boto3.resource("dynamodb", region_name=region).Table(name)


def _when(ts: int) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _printable(text: Any) -> str:
    """No terminal control characters from a stored value."""
    return "".join(c if c.isprintable() else "?" for c in str(text))


def _list(table: Any) -> int:
    rows = pending_queue.deferred_rows(table)
    if not rows:
        print("No deferred Phase 3 books.")
        return 0
    for r in rows:
        print(
            f"{_printable(r['book'])}\t{r['cause']}\tattempts={r['attempts']}"
            f"\t{_when(r['deferred_at'])}\t{_printable(r['reason'])}"
        )
    return 0


def _resume(table: Any, books: Sequence[str]) -> int:
    status = 0
    for book in books:
        if pending_queue.resume(table, book):
            print(
                f"resumed {book}: the next reconcile (within the hour) runs Phase 3 for it"
            )
        else:
            print(f"{book}: no Phase 3 queue row", file=sys.stderr)
            status = 1
    return status


def _parse(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    ap.add_argument("books", nargs="*", help="'list', or the books to resume")
    ap.add_argument("--all", action="store_true", help="resume every deferred book")
    ap.add_argument(
        "--cause", choices=pending_queue.CAUSES, help="with --all: only this cause"
    )
    args = ap.parse_args(argv)
    if args.cause and not args.all:
        ap.error("--cause needs --all")
    if args.all == bool(args.books):
        ap.error("give 'list', one or more books, or --all")
    return args


def _resume_all(table: Any, cause: Optional[str]) -> int:
    books = [
        r["book"]
        for r in pending_queue.deferred_rows(table)
        if not cause or r["cause"] == cause
    ]
    if not books:
        print("No deferred Phase 3 books to resume.")
        return 0
    return _resume(table, books)


def main(argv: Optional[Sequence[str]] = None, table: Any = None) -> int:
    args = _parse(argv)
    table = table if table is not None else _table()
    try:
        if args.books == ["list"]:
            return _list(table)
        if args.all:
            return _resume_all(table, args.cause)
        return _resume(table, args.books)
    except (
        Exception
    ) as e:  # noqa: BLE001 - an operator tool: say what failed, exit non-zero
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
