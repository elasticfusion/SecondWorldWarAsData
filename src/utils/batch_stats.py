"""Timing statistics for every xAI (Grok) Batch API job (owner, 2026-11-10): when it was submitted,
how many requests of which kind, when its first result came back, when it finished, and how it got
there - so batch latency can be measured against time of day, size and request type instead of
guessed.

One ``batch_stats#<batch_id>`` row per batch in the api-cache table (no TTL: a few hundred bytes
per batch, kept for trend analysis; not a run-state prefix, so the e2e reset keeps it):

* ``record_submitted`` - at submit (the in-task path, ``GrokClient``): submitted_at, its UTC hour
  and weekday, requests, requests per cache type, model, book, phase.
* ``record_progress`` - whenever the completed count changes, from whoever is polling (the in-task
  poll every 30 s, the batch-poller Lambda every 5 min - so a batch that outlives its task is still
  timed): first_result_at, completed_at, success/error, and a bounded progress history
  [[epoch, done], ...] that shows the flat stretches. A write happens only on a change.
* ``record_outcome`` - complete / failed / timeout / rejected.

Everything is fail-safe: statistics never break or slow a batch (a failure is logged, once).
``scripts/batch_stats_report.py`` reads the rows (and backfills older batches from the logs).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PREFIX = "batch_stats#"
MAX_PROGRESS_POINTS = 200  # a 99-request batch has at most 99 changes


_TABLES: Dict[str, Any] = {}


def _table() -> Any:
    """The api-cache table, with short timeouts and no retries: a slow DynamoDB must never hold a
    batch's poll loop or the poller Lambda (a lost statistics write is acceptable)."""
    import boto3
    from botocore.config import Config

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    name = os.getenv("CACHE_TABLE") or f"{os.getenv('ENV_NAME', 'dev')}-wwii-api-cache"
    if name not in _TABLES:
        cfg = Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1})
        ddb: Any = boto3.resource("dynamodb", region_name=region, config=cfg)
        _TABLES[name] = ddb.Table(name)
    return _TABLES[name]


def _when(epoch: float) -> Dict[str, Any]:
    t = datetime.fromtimestamp(epoch, timezone.utc)
    return {
        "iso": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hour": t.hour,
        "weekday": t.strftime("%a"),
    }


def _enabled() -> bool:
    return os.getenv("BATCH_STATS", "1") != "0"


def record_submitted(
    batch_id: str,
    *,
    requests: int,
    by_type: Optional[Dict[str, int]] = None,
    name: str = "",
    model: str = "",
    book: str = "",
    phase: str = "",
    now: Optional[float] = None,
    table: Any = None,
) -> None:
    """The batch was accepted by xAI."""
    if not (_enabled() and batch_id):
        return
    try:
        t = time.time() if now is None else now
        w = _when(t)
        (table or _table()).update_item(
            Key={"cache_key": PREFIX + batch_id},
            UpdateExpression=(
                "SET batch_id = :id, batch_name = :name, requests = :n, by_type = :types, "
                "model = :model, book = :book, phase = :phase, submitted_at = :t, "
                "submitted_iso = :iso, submitted_hour_utc = :hour, submitted_weekday = :wd"
            ),
            ExpressionAttributeValues={
                ":id": batch_id,
                ":name": name,
                ":n": int(requests),
                ":types": {k: int(v) for k, v in (by_type or {}).items()},
                ":model": model,
                ":book": book or os.getenv("BOOK_NAME", ""),
                ":phase": phase,
                ":t": int(t),
                ":iso": w["iso"],
                ":hour": w["hour"],
                ":wd": w["weekday"],
            },
        )
        logger.info(
            "OBSERV batch_stats.submitted batch=%s requests=%d hour_utc=%d types=%s",
            batch_id,
            requests,
            w["hour"],
            dict(by_type or {}),
        )
    except Exception as e:  # noqa: BLE001 - statistics never break a batch
        logger.warning(
            "OBSERV batch_stats.write_failed op=submitted err=%s", type(e).__name__
        )


def record_progress(
    batch_id: str,
    done: int,
    total: int,
    success: int = 0,
    error: int = 0,
    *,
    source: str = "",
    submitted_at: Optional[int] = None,
    now: Optional[float] = None,
    table: Any = None,
) -> bool:
    """The batch's completed count, if it changed since the last record. Returns whether it wrote.
    ``submitted_at`` (the poller's job record) fills a row the submit never wrote."""
    if not (_enabled() and batch_id and total):
        return False
    key = {"cache_key": PREFIX + batch_id}
    try:
        t = int(time.time() if now is None else now)
        sets, values = _progress_update(done, total, success, error, source, t)
        if submitted_at:
            _fill_submitted(sets, values, int(submitted_at))
        tbl = table or _table()
        tbl.update_item(  # only on a change
            Key=key,
            UpdateExpression="SET " + ", ".join(sets) + ", batch_id = :id",
            ConditionExpression="attribute_not_exists(last_done) OR last_done <> :done",
            ExpressionAttributeNames={"#err": "error", "#total": "total"},
            ExpressionAttributeValues={**values, ":id": batch_id},
        )
    except Exception as e:  # noqa: BLE001 - statistics never break a batch
        if not _conditional(e):
            logger.warning(
                "OBSERV batch_stats.write_failed op=progress err=%s", type(e).__name__
            )
        return False
    _append_history(tbl, key, t, int(done))
    return True


def _progress_update(
    done: int, total: int, success: int, error: int, source: str, t: int
) -> Tuple[list, Dict[str, Any]]:
    """(SET clauses, values) of a progress change; first result and completion are set once."""
    sets = [
        "last_done = :done",
        "#total = :total",
        "success = :ok",
        "#err = :err",
        "last_progress_at = :t",
        "last_source = :src",
    ]
    values: Dict[str, Any] = {
        ":done": int(done),
        ":total": int(total),
        ":ok": int(success),
        ":err": int(error),
        ":t": t,
        ":src": source,
    }
    if done > 0:
        sets.append("first_result_at = if_not_exists(first_result_at, :t)")
    if done >= total:
        sets.append("completed_at = if_not_exists(completed_at, :t)")
        sets.append("outcome = :outcome")  # a completion supersedes a task-side timeout
        values[":outcome"] = "failed" if error >= total else "complete"
    return sets, values


def _fill_submitted(sets: list, values: Dict[str, Any], submitted_at: int) -> None:
    """Submit time (and its hour/weekday) for a row the submit never wrote; never overwrites."""
    w = _when(submitted_at)
    for name, ph, v in (
        ("submitted_at", ":sub", submitted_at),
        ("submitted_iso", ":subiso", w["iso"]),
        ("submitted_hour_utc", ":subhour", w["hour"]),
        ("submitted_weekday", ":subwd", w["weekday"]),
    ):
        sets.append(f"{name} = if_not_exists({name}, {ph})")
        values[ph] = v


def _append_history(tbl: Any, key: dict, t: int, done: int) -> None:
    """One [epoch, done] point, the history bounded (the timings never depend on it)."""
    try:
        tbl.update_item(
            Key=key,
            UpdateExpression="SET progress = list_append(if_not_exists(progress, :e), :p)",
            ConditionExpression="attribute_not_exists(progress) OR size(progress) < :max",
            ExpressionAttributeValues={
                ":e": [],
                ":p": [[t, done]],
                ":max": MAX_PROGRESS_POINTS,
            },
        )
    except Exception as e:  # noqa: BLE001
        if not _conditional(e):
            logger.warning(
                "OBSERV batch_stats.write_failed op=history err=%s", type(e).__name__
            )


def _conditional(e: Exception) -> bool:
    return "ConditionalCheckFailed" in type(e).__name__ + str(e)


def record_outcome(
    batch_id: str, outcome: str, *, now: Optional[float] = None, table: Any = None
) -> None:
    """How the batch ended when it did not simply complete (timeout, rejected, ...)."""
    if not (_enabled() and batch_id):
        return
    try:
        (table or _table()).update_item(
            Key={"cache_key": PREFIX + batch_id},
            UpdateExpression="SET outcome = :o, ended_at = :t, batch_id = :id",
            ExpressionAttributeValues={
                ":o": outcome,
                ":t": int(time.time() if now is None else now),
                ":id": batch_id,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV batch_stats.write_failed op=outcome err=%s", type(e).__name__
        )


def summarise(row: Dict[str, Any]) -> Dict[str, Any]:
    """Derived timings of one row (minutes), for the report."""
    sub = row.get("submitted_at")
    first, done = row.get("first_result_at"), row.get("completed_at")
    mins = lambda a: (
        round((int(a) - int(sub)) / 60, 1) if (a and sub) else None
    )  # noqa: E731
    hour = row.get("submitted_hour_utc")
    return {
        "batch_id": row.get("batch_id", ""),
        "submitted_iso": row.get("submitted_iso", ""),
        "hour_utc": int(hour) if hour is not None else None,  # DynamoDB returns Decimal
        "weekday": row.get("submitted_weekday", ""),
        "requests": int(row.get("requests") or row.get("total") or 0),
        "by_type": {k: int(v) for k, v in (row.get("by_type") or {}).items()},
        "to_first_result_min": mins(first),
        "to_complete_min": mins(done),
        "done": int(row.get("last_done") or 0),
        "outcome": row.get("outcome") or _open_outcome(row),
        "source": row.get("source", ""),
    }


def _open_outcome(row: Dict[str, Any], now: Optional[float] = None) -> str:
    """A batch with no recorded end: "pending" while young, "unknown" after 48 h (its end was
    never seen - e.g. a backfilled batch whose logs stop before it finished)."""
    if row.get("completed_at"):
        return "complete"
    sub = row.get("submitted_at") or row.get("last_progress_at")
    age = (time.time() if now is None else now) - int(sub) if sub else 0
    return "unknown" if age > 48 * 3600 else "pending"
