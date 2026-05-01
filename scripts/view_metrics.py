"""Pipeline metrics viewer — runs locally or as a Lambda behind API Gateway.

Local:  python3 scripts/view_metrics.py
AWS:    Lambda handler: scripts.view_metrics.handler
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def get_metrics_from_dynamodb(table_name: str, region: str) -> list:
    """Fetch all metrics from DynamoDB."""
    import boto3

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    items = []
    scan_kwargs = {
        "FilterExpression": "begins_with(cache_key, :prefix)",
        "ExpressionAttributeValues": {":prefix": "metrics#"},
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            try:
                items.append(json.loads(item["response"]))
            except (json.JSONDecodeError, KeyError):
                pass
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return sorted(items, key=lambda x: x.get("batch_id", ""), reverse=True)


def get_metrics_from_local(metrics_dir: Path) -> list:
    """Fetch all metrics from local JSON files."""
    items = []
    if not metrics_dir.exists():
        return items
    for f in sorted(metrics_dir.glob("batch_*.json"), reverse=True):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return items


def format_metrics(metrics: list) -> str:
    """Format metrics as a readable report."""
    if not metrics:
        return "No metrics found."

    lines = []
    total_requests = 0
    total_valid = 0
    total_truncated = 0
    total_retried = 0
    total_retry_recovered = 0
    total_retry_failed = 0
    total_poll_seconds = 0.0

    for m in metrics:
        batch_id = m.get("batch_id", "unknown")[:16]
        ts = (
            datetime.fromtimestamp(m.get("poll_seconds", 0)).strftime("%H:%M:%S")
            if m.get("poll_seconds", 0) > 86400
            else f"{m.get('poll_seconds', 0):.0f}s"
        )

        lines.append(f"\nBatch: {batch_id}")
        lines.append(f"  Requests: {m.get('total_requests', 0)}")
        lines.append(f"  Valid: {m.get('valid', 0)}")
        lines.append(f"  Truncated: {m.get('truncated', 0)}")
        lines.append(f"  Empty/Error: {m.get('empty', 0)}")
        lines.append(
            f"  Retried: {m.get('retried', 0)} (recovered: {m.get('retry_recovered', 0)}, failed: {m.get('retry_failed', 0)})"
        )
        lines.append(f"  Poll time: {ts}")

        # Per-request failures
        details = m.get("request_details", [])
        failures = [d for d in details if d.get("status") not in ("valid", "retry_ok")]
        if failures:
            lines.append(f"  Failed requests ({len(failures)}):")
            for d in failures[:5]:
                lines.append(
                    f"    {d.get('status')}: {d.get('cache_type', '')} | {d.get('prompt_preview', '')[:60]}"
                )
            if len(failures) > 5:
                lines.append(f"    ... and {len(failures) - 5} more")

        total_requests += m.get("total_requests", 0)
        total_valid += m.get("valid", 0)
        total_truncated += m.get("truncated", 0)
        total_retried += m.get("retried", 0)
        total_retry_recovered += m.get("retry_recovered", 0)
        total_retry_failed += m.get("retry_failed", 0)
        total_poll_seconds += m.get("poll_seconds", 0)

    lines.insert(0, "=" * 60)
    lines.insert(1, "PIPELINE METRICS SUMMARY")
    lines.insert(2, "=" * 60)
    lines.insert(3, f"Batches: {len(metrics)}")
    lines.insert(4, f"Total requests: {total_requests}")
    lines.insert(
        5, f"Valid: {total_valid} ({total_valid / max(total_requests, 1) * 100:.0f}%)"
    )
    lines.insert(6, f"Truncated: {total_truncated}")
    lines.insert(
        7,
        f"Retried: {total_retried} (recovered: {total_retry_recovered}, failed: {total_retry_failed})",
    )
    lines.insert(8, f"Total poll time: {total_poll_seconds:.0f}s")
    lines.insert(9, "-" * 60)

    return "\n".join(lines)


def handler(event, _context):
    """Lambda handler — returns metrics as JSON or HTML."""
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    table = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")

    metrics = get_metrics_from_dynamodb(table, region)

    accept = event.get("headers", {}).get("Accept", "application/json")
    if "text/html" in accept:
        report = format_metrics(metrics)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": f"<html><body><pre>{report}</pre></body></html>",
        }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {"metrics": [m for m in metrics], "count": len(metrics)}, default=str
        ),
    }


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="View pipeline metrics")
    parser.add_argument(
        "--source", choices=["local", "dynamodb", "auto"], default="auto"
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--table", default="dev-wwii-api-cache")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    metrics = []
    if args.source in ("dynamodb", "auto"):
        try:
            metrics = get_metrics_from_dynamodb(args.table, args.region)
        except Exception as e:
            if args.source == "dynamodb":
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

    if not metrics and args.source in ("local", "auto"):
        metrics = get_metrics_from_local(Path("output/metrics"))

    if args.json:
        print(json.dumps(metrics, indent=2, default=str))
    else:
        print(format_metrics(metrics))


if __name__ == "__main__":
    main()
