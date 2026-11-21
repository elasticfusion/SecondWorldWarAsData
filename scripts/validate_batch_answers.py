#!/usr/bin/env python3
"""Find cached Grok answers stored under another request's key (owner 2026-11-18).

Read-only unless --evict. The cache table is scanned once (about 33k rows, a fraction of a cent).

    # offline: do the answers that carry an identity belong to their key's book?
    AWS_PROFILE=wwii python3 scripts/validate_batch_answers.py --check echo

    # needs GROK_API_KEY: re-pair every batch before the fix by request id, compare with the cache
    AWS_PROFILE=wwii GROK_API_KEY=... python3 scripts/validate_batch_answers.py \\
        --check batches --before 2026-11-11

Exit 0: nothing proven wrong and every batch read. Exit 1: rows proven wrong (listed; --evict removes
them after showing the list and asking you to type their number). Exit 2: no API key. Exit 3: some
batches could not be read (the check is incomplete: an expired batch, or a refused key).
"""

import argparse
import logging
import os
import sys

from src.utils import answer_validation as av


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", choices=["echo", "batches", "all"], default="echo")
    ap.add_argument(
        "--table", default=os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
    )
    ap.add_argument(
        "--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    ap.add_argument(
        "--before", default="2026-11-11", help="batches before this date (YYYY-MM-DD)"
    )
    ap.add_argument(
        "--limit", type=int, default=0, help="at most this many batches (0 = all)"
    )
    ap.add_argument("--evict", action="store_true", help="delete the rows proven wrong")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import boto3

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    report = av.Report()
    if args.check in ("echo", "all"):
        av.check_echo(table, report=report)
    if args.check in ("batches", "all"):
        key = os.environ.get("GROK_API_KEY", "")
        if not key:
            print(
                "GROK_API_KEY is needed to read batch results (retrieval spends nothing)",
                file=sys.stderr,
            )
            return 2
        av.check_batches(
            table, key, av.epoch(args.before), report=report, limit=args.limit
        )
    print(av.format_report(report))
    for key in sorted(set(report.bad)):
        print(f"  wrong: {key!r}")
    if args.evict and report.bad:
        if _confirmed(len(set(report.bad)), args.table):
            av.evict_bad(table, report)
            print(f"Evicted {report.evicted} row(s) from {args.table}")
        else:
            print("Nothing evicted (not confirmed).")
    if report.bad:
        return 1
    return 3 if report.counts.get("batch_unavailable") else 0


def _confirmed(n: int, table: str) -> bool:
    """--evict deletes rows: show them first, then ask for their number. A swapped-looking row can
    be an answer re-asked on purpose after the batch (the cost of a wrong eviction: one request).
    """
    try:
        typed = input(f"Delete these {n} row(s) from {table}? Type {n} to confirm: ")
    except EOFError:
        return False
    return typed.strip() == str(n)


if __name__ == "__main__":
    sys.exit(main())
