"""Pipeline status dashboard — CLI now, web UI later.

Local:  python3 scripts/pipeline_status.py
AWS:    python3 scripts/pipeline_status.py --aws
Future: Import get_pipeline_status() from a Flask/FastAPI route.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENTITY_TYPES = (
    "people",
    "people_groups",
    "places",
    "dates",
    "equipment",
    "weather",
    "bibliography",
)

# ---------------------------------------------------------------------------
# Data collection (reusable from web UI)
# ---------------------------------------------------------------------------


def get_pipeline_status(mode: str = "auto") -> dict[str, Any]:
    """Collect full pipeline status. Returns structured dict suitable for JSON API.

    Args:
        mode: "local", "aws", or "auto" (detect from config.yaml)
    """
    if mode == "auto":
        mode = _detect_mode()

    status: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "phases": {},
        "queues": {},
        "batch_jobs": [],
        "entity_counts": {},
    }

    if mode == "aws":
        _collect_aws_status(status)
    else:
        _collect_local_status(status)

    return status


def _detect_mode() -> str:
    """Detect pipeline mode from config.yaml."""
    try:
        import yaml  # pylint: disable=import-outside-toplevel

        config_path = Path(__file__).parent.parent / "config.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return "aws" if config.get("aws", {}).get("enabled") else "local"
    except (OSError, ValueError):
        return "local"


# ---------------------------------------------------------------------------
# AWS status collection
# ---------------------------------------------------------------------------


def _collect_aws_status(status: dict) -> None:
    """Collect status from AWS (ECS, DynamoDB, S3)."""
    import boto3  # pylint: disable=import-outside-toplevel

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    env = os.environ.get("ENV_NAME", "dev")
    cluster = f"{env}-wwii-pipeline"
    table_name = os.environ.get("CACHE_TABLE", f"{env}-wwii-api-cache")
    bucket = os.environ.get("S3_BUCKET", f"{env}-wwii-data")

    ecs = boto3.client("ecs", region_name=region)
    dynamo = boto3.resource("dynamodb", region_name=region).Table(table_name)
    s3 = boto3.client("s3", region_name=region)

    families = {
        "phase1": f"{env}-wwii-phase1-parse",
        "phase2": f"{env}-wwii-phase2-extract",
        "phase3": f"{env}-wwii-phase3-enrich",
    }

    _collect_phase_status(status, ecs, dynamo, cluster, families)
    _collect_dedup_status(status, s3, bucket)
    _collect_queue_status(status, dynamo)
    _collect_batch_jobs(status, dynamo)
    _collect_entity_counts_s3(status, s3, bucket)


def _collect_phase_status(
    status: dict, ecs: Any, dynamo: Any, cluster: str, families: dict
) -> None:
    """Collect phase lock and task status."""
    for phase, family in families.items():
        lock_key = f"lock#{family}"
        lock = dynamo.get_item(Key={"cache_key": lock_key}).get("Item")
        try:
            tasks = ecs.list_tasks(
                cluster=cluster, family=family, desiredStatus="RUNNING"
            )
            running = tasks.get("taskArns", [])
        except (ecs.exceptions.ClusterNotFoundException, Exception):  # noqa: BLE001
            running = []

        if running:
            phase_status = "RUNNING"
        elif lock:
            phase_status = "STALE_LOCK"
        else:
            phase_status = "IDLE"

        lock_age = None
        if lock:
            try:
                lock_ts = int(lock.get("response", "0"))
                lock_age = int((datetime.now(timezone.utc).timestamp() - lock_ts) / 60)
            except (ValueError, TypeError):
                pass

        status["phases"][phase] = {
            "status": phase_status,
            "task_count": len(running),
            "lock_age_minutes": lock_age,
        }


def _collect_dedup_status(status: dict, s3: Any, bucket: str) -> None:
    """Collect dedup review status and duplicate counts from S3."""
    dedup: dict[str, Any] = {"status": "UNKNOWN", "entity_reviews": {}}

    # Review gate status
    try:
        resp = s3.get_object(Bucket=bucket, Key="dedup/review_status.json")
        review = json.loads(resp["Body"].read())
        dedup["status"] = "COMPLETE" if review.get("complete") else "PENDING"
        dedup["reviewed"] = review.get("reviewed", {})
        last_mod = resp.get("LastModified")
        if last_mod:
            dedup["last_updated"] = last_mod.isoformat()
    except Exception:  # noqa: BLE001
        logger.debug("Failed to read dedup review status", exc_info=True)

    # Duplicate pair counts per entity type
    report_keys = {
        "people": "output/people/duplicate_report.json",
        "places": "output/places/duplicate_report.json",
        "groups": "output/people_groups/duplicate_report.json",
        "equipment": "output/equipment/duplicate_report.json",
    }
    for entity, key in report_keys.items():
        try:
            resp = s3.get_object(Bucket=bucket, Key=key)
            report = json.loads(resp["Body"].read())
            total_pairs = report.get("duplicate_groups", 0)
            dedup["entity_reviews"][entity] = {"pairs": total_pairs}
        except Exception:  # noqa: BLE001
            dedup["entity_reviews"][entity] = {"pairs": 0}

    status["phases"]["dedup_review"] = dedup


def _collect_queue_status(status: dict, dynamo: Any) -> None:
    """Collect pending queue counts from DynamoDB."""
    for queue_key, label in [
        ("pending#content", "content"),
        ("pending#parsed", "parsed"),
    ]:
        try:
            resp = dynamo.get_item(Key={"cache_key": queue_key})
            keys = resp.get("Item", {}).get("keys", [])
            status["queues"][label] = {"count": len(keys), "keys": keys[:10]}
        except Exception:  # noqa: BLE001
            status["queues"][label] = {"count": 0, "keys": []}

    try:
        resp = dynamo.get_item(Key={"cache_key": "manifest#phase2"})
        keys = resp.get("Item", {}).get("keys", [])
        status["queues"]["phase3_manifest"] = {"count": len(keys)}
    except Exception:  # noqa: BLE001
        status["queues"]["phase3_manifest"] = {"count": 0}


def _collect_batch_jobs(status: dict, dynamo: Any) -> None:
    """Collect batch job status from DynamoDB."""
    try:
        resp = dynamo.scan(
            FilterExpression="begins_with(cache_key, :prefix)",
            ExpressionAttributeValues={":prefix": "batch_job#"},
        )
        for item in resp.get("Items", []):
            job = {
                "batch_id": item.get("cache_key", "").replace("batch_job#", ""),
                "status": item.get("status", "unknown"),
                "phase": item.get("phase", "?"),
                "book": item.get("book", "?"),
                "request_count": int(item.get("request_count", 0)),
                "submitted_at": item.get("submitted_at"),
            }
            if job["submitted_at"]:
                age_h = (
                    datetime.now(timezone.utc).timestamp() - int(job["submitted_at"])
                ) / 3600
                job["age_hours"] = round(age_h, 1)
            status["batch_jobs"].append(job)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to collect batch jobs", exc_info=True)


def _collect_entity_counts_s3(status: dict, s3: Any, bucket: str) -> None:
    """Count entity files per type in S3."""
    for entity in ENTITY_TYPES:
        try:
            paginator = s3.get_paginator("list_objects_v2")
            count = 0
            for page in paginator.paginate(
                Bucket=bucket, Prefix=f"output/{entity}/", MaxKeys=1000
            ):
                count += page.get("KeyCount", 0)
            status["entity_counts"][entity] = count
        except Exception:  # noqa: BLE001
            logger.debug("Failed to count %s", entity, exc_info=True)


# ---------------------------------------------------------------------------
# Local status collection
# ---------------------------------------------------------------------------


def _collect_local_status(status: dict) -> None:
    """Collect status from local filesystem."""
    base = Path(__file__).parent.parent
    output = base / "output"

    _collect_local_phases(status, output)
    _collect_local_enrichment(status, output)
    _collect_local_entity_counts(status, output)


def _collect_local_phases(status: dict, output: Path) -> None:
    """Collect Phase 1/2 status from local output files."""
    content_dir = output / "content" if output.exists() else None
    parsed_files = list(content_dir.rglob("*-parsed.json")) if content_dir else []
    event_files = list(content_dir.rglob("*-event.json")) if content_dir else []

    missing_events = []
    for pf in parsed_files:
        ef = pf.parent / pf.name.replace("-parsed.json", "-event.json")
        if not ef.exists():
            missing_events.append(pf.name)

    status["phases"]["phase1"] = {
        "status": "COMPLETE" if parsed_files else "NOT_RUN",
        "parsed_files": len(parsed_files),
    }
    status["phases"]["phase2"] = {
        "status": (
            "INCOMPLETE"
            if missing_events
            else ("COMPLETE" if event_files else "NOT_RUN")
        ),
        "event_files": len(event_files),
        "missing_events": missing_events[:10],
        "missing_count": len(missing_events),
    }


def _collect_local_enrichment(status: dict, output: Path) -> None:
    """Collect Phase 3 enrichment status from local people files."""
    people_dir = output / "people"
    if not people_dir.exists():
        status["phases"]["phase3"] = {"status": "NOT_RUN"}
        return

    skip = {"index.json", "duplicate_report.json", "not_duplicates.json"}
    total = enriched = not_found = pending = 0

    for f in people_dir.glob("*.json"):
        if f.name in skip:
            continue
        total += 1
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            es = data.get("enrichment_status")
            if es == "enriched":
                enriched += 1
            elif es == "not_found":
                not_found += 1
            else:
                pending += 1
        except (OSError, json.JSONDecodeError):
            pending += 1

    status["phases"]["phase3"] = {
        "status": "COMPLETE" if pending == 0 and total > 0 else "INCOMPLETE",
        "people_total": total,
        "people_enriched": enriched,
        "people_not_found": not_found,
        "people_pending": pending,
    }


def _collect_local_entity_counts(status: dict, output: Path) -> None:
    """Count entity files per type on local filesystem."""
    for entity in ENTITY_TYPES:
        d = output / entity
        if d.exists():
            count = sum(1 for f in d.glob("*.json") if f.name != "index.json")
            status["entity_counts"][entity] = count


# ---------------------------------------------------------------------------
# CLI display
# ---------------------------------------------------------------------------

_PHASE_ICONS = {
    "RUNNING": "🟢",
    "IDLE": "⚪",
    "COMPLETE": "✅",
    "INCOMPLETE": "🟡",
    "PENDING": "🟡",
    "NOT_RUN": "⚫",
    "STALE_LOCK": "🔴",
    "UNKNOWN": "❓",
}


def format_status(status: dict) -> str:
    """Format status dict as human-readable text."""
    ts = status["timestamp"][:19].replace("T", " ")
    lines = [
        f"Pipeline Status ({ts} UTC)",
        "=" * 50,
        f"Mode: {status['mode'].upper()}",
        "",
        "Phases:",
    ]

    _format_phases(lines, status)
    _format_dedup_gate(lines, status)
    _format_queues(lines, status)
    _format_batch_jobs(lines, status)
    _format_entity_counts(lines, status)

    return "\n".join(lines)


def _format_phases(lines: list, status: dict) -> None:
    """Append phase status lines."""
    for phase, info in status["phases"].items():
        icon = _PHASE_ICONS.get(info["status"], "❓")
        label = phase.replace("_", " ").title()
        extra = _get_phase_extra(info)
        lines.append(f"  {icon} {label:20s} {info['status']}{extra}")


def _get_phase_extra(info: dict) -> str:
    """Build extra context string for a phase."""
    if info.get("lock_age_minutes"):
        return f" (lock age: {info['lock_age_minutes']}m)"
    if info.get("task_count"):
        return f" ({info['task_count']} task(s))"
    if info.get("missing_count"):
        return f" ({info['missing_count']} missing event files)"
    if info.get("people_pending"):
        enriched = info.get("people_enriched", 0)
        return f" ({info['people_pending']} pending, {enriched} enriched)"
    if info.get("entity_reviews"):
        total = sum(v.get("pairs", 0) for v in info["entity_reviews"].values())
        if total:
            return f" ({total} duplicate pairs to review)"
    return ""


def _format_dedup_gate(lines: list, status: dict) -> None:
    """Append dedup gate detail lines."""
    dedup = status.get("phases", {}).get("dedup_review", {})
    if not dedup.get("entity_reviews"):
        return
    lines.append("")
    lines.append("Dedup Gate:")
    gate_status = dedup.get("status", "UNKNOWN")
    blocking = " (blocking Phase 3)" if gate_status == "PENDING" else ""
    lines.append(f"  Status:           {gate_status}{blocking}")
    for entity, info in dedup.get("entity_reviews", {}).items():
        pairs = info.get("pairs", 0)
        reviewed = dedup.get("reviewed", {}).get(entity)
        if reviewed:
            mark = "reviewed ✓"
        elif pairs:
            mark = f"{pairs} pairs awaiting review"
        else:
            mark = "no duplicates"
        lines.append(f"  {entity.title():18s} {mark}")
    if dedup.get("last_updated"):
        ts = dedup["last_updated"][:19].replace("T", " ")
        lines.append(f"  Last updated:     {ts} UTC")


def _format_queues(lines: list, status: dict) -> None:
    """Append queue status lines."""
    if not status.get("queues"):
        return
    lines.append("")
    lines.append("Pending Work:")
    for queue, info in status["queues"].items():
        count = info.get("count", 0)
        label = queue.replace("_", " ").title()
        lines.append(f"  {label:20s} {count} item(s)")
        if info.get("keys") and count > 0:
            for k in info["keys"][:5]:
                lines.append(f"    • {k.split('/')[-1]}")


def _format_batch_jobs(lines: list, status: dict) -> None:
    """Append batch job status lines."""
    if not status.get("batch_jobs"):
        return
    lines.append("")
    lines.append("Batch Jobs:")
    for job in sorted(
        status["batch_jobs"],
        key=lambda j: j.get("submitted_at", 0),
        reverse=True,
    ):
        age = f"{job.get('age_hours', '?')}h ago" if job.get("age_hours") else ""
        lines.append(
            f"  {job['status']:10s} {job['phase']}/{job['book']} "
            f"({job['request_count']} reqs) {age}"
        )


def _format_entity_counts(lines: list, status: dict) -> None:
    """Append entity count lines."""
    if not status.get("entity_counts"):
        return
    lines.append("")
    lines.append("Entity Counts:")
    for entity, count in sorted(status["entity_counts"].items()):
        lines.append(f"  {entity:20s} {count:,}")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def handler(event, _context):
    """Lambda handler — returns JSON for API Gateway."""
    params = event.get("queryStringParameters") or {}
    mode = params.get("mode", "aws")
    status = get_pipeline_status(mode)
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(status, default=str),
    }


def main():
    """CLI entry point."""
    import argparse  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(description="Pipeline status dashboard")
    parser.add_argument("--aws", action="store_true", help="Force AWS mode")
    parser.add_argument("--local", action="store_true", help="Force local mode")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    mode = "aws" if args.aws else "local" if args.local else "auto"
    status = get_pipeline_status(mode)

    if args.json:
        print(json.dumps(status, indent=2, default=str))
    else:
        print(format_status(status))


if __name__ == "__main__":
    main()
