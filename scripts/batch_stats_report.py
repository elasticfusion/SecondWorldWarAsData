#!/usr/bin/env python3
"""Report the xAI Batch API timing statistics (src/utils/batch_stats): every batch's submit time,
size, time to first result and to completion, and the same grouped by UTC hour of submission and
by size - to see when large batches come back fastest.

    python scripts/batch_stats_report.py                     # the table + by-hour summary
    python scripts/batch_stats_report.py --backfill-days 14  # first, add batches seen in the logs

The backfill reconstructs batches from CloudWatch Logs (the pipeline task polls every 30 s and logs
"Batch <id>: <done>/<total> complete"; the batch-poller Lambda logs every 5 min). Submit time is the
first progress line (within ~30 s of the submit); it never overwrites a value recorded live.
Logs are kept 14 days, so run it before older batches age out.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import batch_stats  # noqa: E402

_LINE = re.compile(r"Batch (batch_[0-9a-f-]{6,}): (\d+)/(\d+)")


def rows_from_log_lines(lines: Iterable[Tuple[int, str]]) -> Dict[str, Dict[str, Any]]:
    """{batch id: {"submitted_at", "first_result_at", "completed_at", "total", "done"}} from
    (epoch seconds, message) log lines. A 12-character id (the poller logs a prefix) is matched to
    the full id seen elsewhere, else kept as a prefix."""
    seen: Dict[str, List[Tuple[int, int, int]]] = defaultdict(list)
    for ts, msg in lines:
        m = _LINE.search(msg or "")
        if m:
            seen[m.group(1)].append((int(ts), int(m.group(2)), int(m.group(3))))
    full = [b for b in seen if len(b) > 20]
    merged: Dict[str, List[Tuple[int, int, int]]] = defaultdict(list)
    for bid, pts in seen.items():
        target = (
            next((f for f in full if f.startswith(bid)), bid) if len(bid) <= 20 else bid
        )
        merged[target] += pts
    out = {}
    for bid, pts in merged.items():
        pts.sort()
        total = max(p[2] for p in pts)
        if not total:
            continue
        first = next((t for t, d, _ in pts if d > 0), None)
        fin = next((t for t, d, _ in pts if d >= total), None)
        out[bid] = {
            "submitted_at": pts[0][0],
            "first_result_at": first,
            "completed_at": fin,
            "total": total,
            "done": max(p[1] for p in pts),
        }
    return out


def _query_logs(group: str, days: int) -> List[Tuple[int, str]]:
    import boto3

    logs = boto3.client("logs")
    q = logs.start_query(
        logGroupName=group,
        startTime=int(time.time()) - days * 86400,
        endTime=int(time.time()),
        limit=10000,
        queryString=(
            "fields @timestamp, @message | filter @message like /Batch batch_/ "
            "| parse @message /Batch (?<bid>batch_[0-9a-f-]+): (?<d>[0-9]+)\\/(?<n>[0-9]+)/ "
            "| filter ispresent(bid) "
            "| stats min(@timestamp) as t by bid, d, n | sort t asc"
        ),
    )["queryId"]
    while True:
        r = logs.get_query_results(queryId=q)
        if r["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            break
        time.sleep(2)
    out = []
    for row in r.get("results", []):
        f = {c["field"]: c["value"] for c in row}
        t = datetime.strptime(f["t"][:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        out.append((int(t.timestamp()), f"Batch {f['bid']}: {f['d']}/{f['n']}"))
    return out


def backfill(days: int, env: str, table: Any) -> int:
    """Add the batches the logs show, never overwriting a live value. Returns how many."""
    lines = _query_logs(f"/ecs/{env}-wwii-pipeline", days) + _query_logs(
        f"/aws/lambda/{env}-wwii-batch-poller", days
    )
    found = rows_from_log_lines(lines)
    for bid, r in found.items():
        if (
            len(bid) <= 20
        ):  # only the poller's short id was seen: cannot be keyed reliably
            continue
        names = {"#src": "source", "#total": "total"}
        # "approximate": the first progress line, not the submit itself
        sets, values = ["#src = if_not_exists(#src, :src)", "batch_id = :id"], {
            ":src": "backfill_logs (submit time approximate)",
            ":id": bid,
        }
        w = batch_stats._when(r["submitted_at"])  # pylint: disable=protected-access
        fields = {
            "submitted_at": r["submitted_at"],
            "submitted_iso": w["iso"],
            "submitted_hour_utc": w["hour"],
            "submitted_weekday": w["weekday"],
            "requests": r["total"],
            "total": r["total"],
            "last_done": r["done"],
            "first_result_at": r["first_result_at"],
            "completed_at": r["completed_at"],
        }
        for i, (k, v) in enumerate(fields.items()):
            if v is None:
                continue
            n = "#total" if k == "total" else k
            sets.append(f"{n} = if_not_exists({n}, :v{i})")
            values[f":v{i}"] = v
        table.update_item(
            Key={"cache_key": batch_stats.PREFIX + bid},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    return len(found)


def load_rows(table: Any) -> List[Dict[str, Any]]:
    from boto3.dynamodb.conditions import Attr

    items, kw = [], {
        "FilterExpression": Attr("cache_key").begins_with(batch_stats.PREFIX)
    }
    while True:
        r = table.scan(**kw)
        items += r["Items"]
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def _median(xs: List[float]) -> Optional[float]:
    return round(statistics.median(xs), 1) if xs else None


def report(rows: List[Dict[str, Any]]) -> str:
    s = sorted(
        (batch_stats.summarise(r) for r in rows), key=lambda x: x["submitted_iso"]
    )
    out = [
        f"{'submitted (UTC)':20} {'batch':16} {'reqs':>5} {'1st result':>11} "
        f"{'complete':>9}  outcome"
    ]
    for r in s:
        fmt = lambda v: f"{v:.0f} min" if v is not None else "-"  # noqa: E731
        out.append(
            f"{r['submitted_iso'][:16]:20} {r['batch_id'][:16]:16} {r['requests']:>5} "
            f"{fmt(r['to_first_result_min']):>11} {fmt(r['to_complete_min']):>9}  "
            f"{r['outcome']} {r['done']}/{r['requests']}"
        )
    out += [
        "",
        _grouped(s, "hour_utc", "by UTC hour submitted"),
        _grouped(s, "size", "by size"),
    ]
    return "\n".join(out)


def _grouped(s: List[Dict[str, Any]], key: str, title: str) -> str:
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for r in s:
        k = r["hour_utc"] if key == "hour_utc" else _size(r["requests"])
        groups[k].append(r)
    lines = [
        f"{title}: batches | median min to first result | median min to complete | worst"
    ]
    order = ["1-9", "10-49", "50-199", "200+"]
    for k in sorted(
        groups,
        key=lambda x: (x is None, order.index(x) if x in order else x or 0),
    ):
        g = groups[k]
        firsts = [
            r["to_first_result_min"] for r in g if r["to_first_result_min"] is not None
        ]
        dones = [r["to_complete_min"] for r in g if r["to_complete_min"] is not None]
        label = f"{k:02d}:00" if key == "hour_utc" and k is not None else str(k)
        lines.append(
            f"  {label:>9}  {len(g):>3} | {_median(firsts)} | {_median(dones)} | "
            f"{max(dones) if dones else None} ({len(g) - len(dones)} unfinished)"
        )
    return "\n".join(lines)


def _size(n: int) -> str:
    return "1-9" if n < 10 else "10-49" if n < 50 else "50-199" if n < 200 else "200+"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--env", default="dev")
    ap.add_argument("--backfill-days", type=int, default=0)
    args = ap.parse_args(argv)
    import os

    os.environ.setdefault("CACHE_TABLE", f"{args.env}-wwii-api-cache")
    table = batch_stats._table()  # pylint: disable=protected-access
    if args.backfill_days:
        print(
            f"backfilled {backfill(args.backfill_days, args.env, table)} batches from the logs"
        )
    print(report(load_rows(table)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
