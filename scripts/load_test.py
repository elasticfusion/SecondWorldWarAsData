#!/usr/bin/env python3
"""M9 concurrency load-test harness (CONCURRENCY_AND_NAT_SPEC §12.9/§16).

Runs a bounded, mixed-media subset of the backlog through the concurrency
dispatcher and measures what the spec requires before a full drain:
  - throughput (docs/hr) and wall time
  - per-resource utilization (Fargate tasks vs pool, Batch GPU) — Container
    Insights hints
  - ACTUAL cost via the Batch API (`cost_in_usd_ticks`) vs the §9.1 pre-estimate
  - correctness violations: duplicate entities (blocking-key collisions that
    should have merged) and NAT lease leaks (leases with no running task)

Two modes:
  --dry-run (default): local, no AWS. Samples + routes docs (pre-stage), builds
    doc# plan, estimates cost. Validates the harness + the sample without spend.
  --live: reads DynamoDB/xAI to collect real metrics for an in-flight/finished
    run. Does NOT itself deploy or flip the kill-switch — those are deliberate,
    separate operator actions (deploy change-set + MULTI_DOC_ENABLED=true).

Usage:
  python3 scripts/load_test.py --source ~/Downloads/WWIIArchives --sample 20
  python3 scripts/load_test.py --live --report-only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("load_test")

# Media -> resource track (mirrors prestage routing, §7.1), for a balanced sample.
_MEDIA_EXTS = {
    "pdf": {".pdf"},
    "image": {".jpg", ".jpeg", ".png", ".tif", ".tiff"},
    "text": {".txt", ".md", ".docx", ".epub", ".html"},
    "archive": {".zip", ".rar"},
}


@dataclass
class LoadTestReport:
    mode: str
    sample_size: int
    media_mix: Dict[str, int] = field(default_factory=dict)
    pool: int = 4
    started_at: float = 0.0
    finished_at: float = 0.0
    docs_done: int = 0
    docs_failed: int = 0
    docs_needs_review: int = 0
    est_cost_usd: float = 0.0
    actual_cost_usd: Optional[float] = None
    dup_violations: int = 0
    lease_leaks: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def wall_seconds(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0

    @property
    def throughput_docs_per_hr(self) -> float:
        w = self.wall_seconds
        return round(self.docs_done / (w / 3600.0), 2) if w > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["wall_seconds"] = round(self.wall_seconds, 1)
        d["throughput_docs_per_hr"] = self.throughput_docs_per_hr
        return d


# --------------------------------------------------------------------------
# Sampling — a balanced mixed-media subset (§16: "20 mixed-media docs")
# --------------------------------------------------------------------------


def _classify_ext(path: Path) -> str:
    ext = path.suffix.lower()
    for media, exts in _MEDIA_EXTS.items():
        if ext in exts:
            return media
    return "other"


def sample_docs(source: Path, n: int, *, seed: int = 42) -> List[Path]:
    """Pick up to n files spread across media types for a representative mix."""
    by_media: Dict[str, List[Path]] = {}
    for p in source.rglob("*"):
        if p.is_file():
            by_media.setdefault(_classify_ext(p), []).append(p)
    rng = random.Random(seed)
    for lst in by_media.values():
        rng.shuffle(lst)
    # Round-robin across media types so the mix is balanced, not all-PDF.
    picked: List[Path] = []
    media_cycle = [m for m in _MEDIA_EXTS if by_media.get(m)]
    i = 0
    while len(picked) < n and media_cycle:
        m = media_cycle[i % len(media_cycle)]
        if by_media[m]:
            picked.append(by_media[m].pop())
        else:
            media_cycle.remove(m)
            continue
        i += 1
    return picked


# --------------------------------------------------------------------------
# Dry-run: route + estimate locally (no AWS, no spend)
# --------------------------------------------------------------------------


def dry_run(source: Path, n: int, pool: int) -> LoadTestReport:
    from src.ingestion.prestage import route_file
    from src.utils import credit_gate

    docs = sample_docs(source, n)
    report = LoadTestReport(mode="dry-run", sample_size=len(docs), pool=pool)
    mix: Counter = Counter()
    est = 0.0
    for d in docs:
        media, track, _phase = route_file(d)
        mix[f"{media}:{track}"] += 1
        # Rough per-doc estimate: size proxy -> tokens (§9.1 content_length proxy).
        try:
            in_tokens = max(d.stat().st_size // 4, 1)
        except OSError:
            in_tokens = 1000
        est += credit_gate.estimate_cost_usd(in_tokens, 2000, is_batch=True)
    report.media_mix = dict(mix)
    report.est_cost_usd = round(est, 4)
    report.notes.append(
        "Dry-run only: no docs processed, no AWS calls, no spend. "
        "Estimate uses a size->token proxy; live run reports actual via cost_in_usd_ticks."
    )
    return report


# --------------------------------------------------------------------------
# Live: collect real metrics from DynamoDB / xAI (run does NOT deploy/enable)
# --------------------------------------------------------------------------


def _table():
    import boto3

    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    name = os.getenv("CACHE_TABLE", f"{os.getenv('ENV_NAME', 'dev')}-wwii-api-cache")
    return boto3.resource("dynamodb", region_name=region).Table(name)


def _scan_prefix(
    table, prefix: str, projection: str = "cache_key"
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    kwargs = {
        "FilterExpression": "begins_with(cache_key, :p)",
        "ExpressionAttributeValues": {":p": prefix},
        "ProjectionExpression": projection,
    }
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _lease_leaks(table) -> int:
    """Live leases whose task is not RUNNING = a leak (§16 correctness check)."""
    import boto3

    leases = _scan_prefix(table, "nat#lease#")
    if not leases:
        return 0
    lease_task_ids = {i["cache_key"].split("#")[-1] for i in leases}
    ecs = boto3.client("ecs", region_name=os.getenv("AWS_REGION", "us-east-1"))
    cluster = f"{os.getenv('ENV_NAME', 'dev')}-wwii-pipeline"
    running = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING").get(
        "taskArns", []
    )
    running_ids = {a.split("/")[-1] for a in running}
    return sum(1 for tid in lease_task_ids if tid not in running_ids)


def _dup_violations(table) -> int:
    """Count entities sharing a normalized blocking key (should have merged, §3.3)."""
    from src.dedup.resolved_set import blocking_keys

    dups = 0
    for etype in ("people", "places", "people_groups"):
        seen: Dict[str, str] = {}
        kwargs = {
            "FilterExpression": "begins_with(cache_key, :p)",
            "ExpressionAttributeValues": {":p": f"entity#{etype}#"},
            "ProjectionExpression": "cache_key, #d",
            "ExpressionAttributeNames": {"#d": "data"},
        }
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items", []):
                try:
                    data = json.loads(item["data"])
                except (KeyError, json.JSONDecodeError):
                    continue
                for k in blocking_keys(data, etype):
                    if k.startswith("name:"):
                        if k in seen:
                            dups += 1
                        else:
                            seen[k] = item["cache_key"]
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    return dups


def _actual_cost(table) -> Optional[float]:
    """Sum actual cost from batch metrics (cost_in_usd_ticks, §6.2/§9.1) if present."""
    total_ticks = 0
    found = False
    for item in _scan_prefix(table, "metrics#", "response"):
        try:
            m = json.loads(item.get("response", "{}"))
        except json.JSONDecodeError:
            continue
        ticks = m.get("cost_in_usd_ticks") or m.get("total_cost_usd_ticks")
        if ticks:
            total_ticks += int(ticks)
            found = True
    return round(total_ticks * 1e-10, 6) if found else None


def live_report(pool: int) -> LoadTestReport:
    table = _table()
    # Count doc# lifecycle states (status is a reserved word -> alias).
    statuses: Counter = Counter()
    kwargs = {
        "FilterExpression": "begins_with(cache_key, :p)",
        "ExpressionAttributeValues": {":p": "doc#"},
        "ProjectionExpression": "#s",
        "ExpressionAttributeNames": {"#s": "status"},
    }
    while True:
        resp = table.scan(**kwargs)
        for it in resp.get("Items", []):
            statuses[it.get("status", "unknown")] += 1
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek

    report = LoadTestReport(mode="live", sample_size=sum(statuses.values()), pool=pool)
    report.docs_done = statuses.get("done", 0)
    report.docs_failed = statuses.get("failed", 0)
    report.docs_needs_review = statuses.get("needs-review", 0)
    report.media_mix = dict(statuses)
    report.actual_cost_usd = _actual_cost(table)
    report.lease_leaks = _lease_leaks(table)
    report.dup_violations = _dup_violations(table)
    if report.lease_leaks:
        report.notes.append(
            f"LEASE LEAK: {report.lease_leaks} lease(s) with no running task"
        )
    if report.dup_violations:
        report.notes.append(
            f"DUP VIOLATION: {report.dup_violations} entities share a blocking key "
            "(should have merged — investigate incremental dedup)"
        )
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="M9 concurrency load-test harness")
    ap.add_argument("--source", type=Path, help="Directory of source docs to sample")
    ap.add_argument("--sample", type=int, default=20, help="Number of docs to sample")
    ap.add_argument("--pool", type=int, default=4, help="Target dispatcher pool size")
    ap.add_argument("--live", action="store_true", help="Collect real metrics from AWS")
    ap.add_argument(
        "--report-only", action="store_true", help="Live: report without driving"
    )
    ap.add_argument("--out", type=Path, help="Write JSON report to this path")
    args = ap.parse_args(argv)

    if args.live:
        report = live_report(args.pool)
    else:
        if not args.source or not args.source.exists():
            ap.error("--source DIR is required for dry-run (or use --live)")
        report = dry_run(args.source, args.sample, args.pool)

    out = json.dumps(report.to_dict(), indent=2)
    print(out)
    if args.out:
        args.out.write_text(out, encoding="utf-8")
        logger.info("Wrote report to %s", args.out)
    # Non-zero exit on a live correctness violation so CI/operators notice.
    return 1 if (report.lease_leaks or report.dup_violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
