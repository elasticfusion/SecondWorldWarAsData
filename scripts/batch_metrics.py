#!/usr/bin/env python3
"""Grok Batch API metrics — data before we act on the Phase-2 container split.

WHAT THE API CAN AND CANNOT GIVE US (verified against api.x.ai 2026-10-19):
  * GET /v1/batches  -> cross-batch snapshot: batch_id, name, create_time (DATE ONLY, no
    time-of-day), expire_time, cancel_time, and state{num_requests,num_pending,num_success,
    num_error,num_cancelled}.
  * GET /v1/batches/{id} -> same fields. There is NO completion/finish timestamp and create_time
    has no time component, so BATCH DURATION CANNOT BE DERIVED FROM THE API.
  => "average time to complete a batch" must come from OUR OWN timing, which we record in the
     batch_job# DynamoDB items (submitted_at -> completed_at, unix seconds) via the job_queue /
     batch-poller path. "Average time to complete a request WITHIN a batch" is NOT exposed by xAI
     (batches are processed as a unit on xAI's schedule; per-request timing is not meaningful/
     available) — we report batch-level THROUGHPUT (requests / wall-clock) instead.

SOURCES:
  * API (live)     -> status distribution + success/error rates across all your batches.
  * batch_job#     -> durations (completed_at - submitted_at), throughput, per-phase/book breakdown.

USAGE:
  python scripts/batch_metrics.py                 # human-readable + OBSERV line
  python scripts/batch_metrics.py --json          # machine-readable
  python scripts/batch_metrics.py --phase phase2  # filter duration stats to one phase

Credentials: uses the same AWS creds the pipeline uses; Grok key from Secrets Manager
(dev-wwii-pipeline/grok-api-key) or $GROK_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("batch_metrics")

API_BASE = "https://api.x.ai/v1"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def _grok_key() -> str:
    key = os.getenv("GROK_API_KEY", "").strip()
    if key:
        return key
    import boto3

    sm = boto3.client(
        "secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1")
    )
    secret_id = os.getenv("GROK_SECRET_ID", "dev-wwii-pipeline/grok-api-key")
    val = sm.get_secret_value(SecretId=secret_id)["SecretString"]
    try:
        return (json.loads(val).get("GROK_API_KEY") or val).strip()
    except (json.JSONDecodeError, AttributeError):
        return val.strip()


# ---------------------------------------------------------------------------
# API side: cross-batch snapshot (counts + status; NO durations available)
# ---------------------------------------------------------------------------
def list_batches(
    key: str, limit: int = 100, max_pages: int = 20
) -> List[Dict[str, Any]]:
    """Page through GET /v1/batches. Returns the raw batch objects."""
    out: List[Dict[str, Any]] = []
    token: Optional[str] = None
    for _ in range(max_pages):
        url = f"{API_BASE}/batches?limit={limit}"
        if token:
            url += f"&pagination_token={token}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed host
            d = json.load(r)
        out.extend(d.get("batches", []))
        token = d.get("pagination_token")
        if not token:
            break
    return out


def api_snapshot(batches: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate the live API snapshot: status distribution + request success/error rates."""
    status_counts = {"completed": 0, "in_progress": 0, "cancelled": 0, "empty": 0}
    req_total = req_success = req_error = req_pending = req_cancelled = 0
    for b in batches:
        st = b.get("state", {}) or {}
        n = st.get("num_requests", 0)
        done = st.get("num_success", 0) + st.get("num_error", 0)
        req_total += n
        req_success += st.get("num_success", 0)
        req_error += st.get("num_error", 0)
        req_pending += st.get("num_pending", 0)
        req_cancelled += st.get("num_cancelled", 0)
        if b.get("cancel_time"):
            status_counts["cancelled"] += 1
        elif n == 0:
            status_counts["empty"] += 1
        elif done >= n and n > 0:
            status_counts["completed"] += 1
        else:
            status_counts["in_progress"] += 1
    return {
        "n_batches": len(batches),
        "status_counts": status_counts,
        "requests": {
            "total": req_total,
            "success": req_success,
            "error": req_error,
            "pending": req_pending,
            "cancelled": req_cancelled,
            "success_rate": round(req_success / req_total, 4) if req_total else None,
            "error_rate": round(req_error / req_total, 4) if req_total else None,
        },
    }


# ---------------------------------------------------------------------------
# Our side: durations from batch_job# (the ONLY authoritative timing source)
# ---------------------------------------------------------------------------
def duration_stats(phase: Optional[str] = None) -> Dict[str, Any]:
    """Compute batch wall-clock duration + throughput stats from batch_job# DynamoDB items.

    Only jobs with a completed_at > 0 contribute to durations (i.e. jobs whose completion was
    observed via the poller/retrieve path). Returns counts of how many lacked timing so the
    coverage is honest."""
    import boto3
    from boto3.dynamodb.conditions import Attr

    table_name = os.getenv("CACHE_TABLE", "dev-wwii-api-cache")
    t = boto3.resource(
        "dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1")
    ).Table(table_name)
    items: List[Dict[str, Any]] = []
    kwargs = {"FilterExpression": Attr("cache_key").begins_with("batch_job#")}
    resp = t.scan(**kwargs)
    items += resp.get("Items", [])
    while "LastEvaluatedKey" in resp:
        resp = t.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items += resp.get("Items", [])

    durations: List[float] = []
    throughputs: List[float] = []
    pending = missing_timing = counted = 0
    by_phase: Dict[str, int] = {}
    for it in items:
        ph = it.get("phase", "?")
        if phase and ph != phase:
            continue
        by_phase[ph] = by_phase.get(ph, 0) + 1
        sub = int(it.get("submitted_at", 0) or 0)
        comp = int(it.get("completed_at", 0) or 0)
        reqs = int(it.get("request_count", 0) or 0)
        if it.get("status") in ("pending", "ready"):
            pending += 1
        if sub and comp and comp > sub:
            dur = comp - sub
            durations.append(dur)
            if reqs:
                throughputs.append(reqs / (dur / 3600.0))  # requests per hour
            counted += 1
        else:
            missing_timing += 1

    def _pct(xs: List[float], p: float) -> Optional[float]:
        if not xs:
            return None
        xs = sorted(xs)
        k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
        return round(xs[k], 1)

    return {
        "jobs_total": sum(by_phase.values()),
        "by_phase": by_phase,
        "pending_now": pending,
        "with_timing": counted,
        "missing_timing": missing_timing,
        "duration_seconds": {
            "avg": round(statistics.fmean(durations), 1) if durations else None,
            "p50": _pct(durations, 50),
            "p95": _pct(durations, 95),
            "min": round(min(durations), 1) if durations else None,
            "max": round(max(durations), 1) if durations else None,
        },
        "throughput_reqs_per_hour": {
            "avg": round(statistics.fmean(throughputs), 1) if throughputs else None,
            "p50": _pct(throughputs, 50),
        },
    }


def _fmt_secs(s: Optional[float]) -> str:
    if s is None:
        return "n/a"
    if s < 90:
        return f"{s:.0f}s"
    if s < 5400:
        return f"{s/60:.1f}m"
    return f"{s/3600:.2f}h"


def main() -> None:
    ap = argparse.ArgumentParser(description="Grok Batch API metrics")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--phase", help="filter duration stats to one phase (phase2/phase3)"
    )
    ap.add_argument("--limit", type=int, default=100, help="batches per API page")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report: Dict[str, Any] = {}
    try:
        key = _grok_key()
        batches = list_batches(key, limit=args.limit)
        report["api"] = api_snapshot(batches)
    except Exception as e:  # noqa: BLE001 - metrics must not hard-fail
        report["api"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        report["durations"] = duration_stats(phase=args.phase)
    except Exception as e:  # noqa: BLE001
        report["durations"] = {"error": f"{type(e).__name__}: {e}"}

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        a = report.get("api", {})
        d = report.get("durations", {})
        print("=" * 60)
        print("GROK BATCH API METRICS")
        print("=" * 60)
        print("\n[API snapshot — all batches on this key]")
        if "error" in a:
            print(f"  (unavailable: {a['error']})")
        else:
            print(f"  batches: {a['n_batches']}  status: {a['status_counts']}")
            r = a["requests"]
            print(
                f"  requests: total={r['total']} success={r['success']} "
                f"error={r['error']} pending={r['pending']} "
                f"success_rate={r['success_rate']}"
            )
        print("\n[Batch wall-clock durations — from our batch_job# records]")
        print(
            "  (xAI exposes no completion timestamp; durations are OUR submitted->completed)"
        )
        if "error" in d:
            print(f"  (unavailable: {d['error']})")
        else:
            ds = d["duration_seconds"]
            print(
                f"  jobs={d['jobs_total']} by_phase={d['by_phase']} "
                f"pending_now={d['pending_now']}"
            )
            print(
                f"  timing coverage: {d['with_timing']} with / {d['missing_timing']} without"
                " (without = never observed completing via the poller path)"
            )
            print(
                f"  duration: avg={_fmt_secs(ds['avg'])} p50={_fmt_secs(ds['p50'])} "
                f"p95={_fmt_secs(ds['p95'])} min={_fmt_secs(ds['min'])} max={_fmt_secs(ds['max'])}"
            )
            print(
                f"  throughput: avg={d['throughput_reqs_per_hour']['avg']} reqs/hr "
                f"p50={d['throughput_reqs_per_hour']['p50']} reqs/hr"
            )
        # One OBSERV line for log-based tracking (consistent with the pipeline's OBSERV schema).
        ds = d.get("duration_seconds", {}) if "error" not in d else {}
        r = a.get("requests", {}) if "error" not in a else {}
        logger.info(
            "OBSERV batch.stats n_batches=%s req_total=%s success_rate=%s "
            "dur_avg_s=%s dur_p50_s=%s dur_p95_s=%s timing_cov=%s/%s",
            a.get("n_batches"),
            r.get("total"),
            r.get("success_rate"),
            ds.get("avg"),
            ds.get("p50"),
            ds.get("p95"),
            d.get("with_timing"),
            d.get("jobs_total"),
        )


if __name__ == "__main__":
    main()
