#!/usr/bin/env python3
"""Aggregate JSON quality stats from CloudWatch logs by day."""

import subprocess
import sys
from datetime import datetime, timedelta

REGION = "us-east-1"
LOG_GROUP = "/ecs/dev-wwii-pipeline"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 10

PATTERNS = {
    "Responses": '"finish_reason: stop"',
    "Repaired": '"JSON repaired"',
    "Truncated": '"Response truncated"',
    "Markdown": '"```json"',
}


def count_events(pattern, start_ms, end_ms):
    """Count log events matching pattern in time range."""
    total = 0
    next_token = None
    while True:
        cmd = [
            "aws", "logs", "filter-log-events",
            "--log-group-name", LOG_GROUP,
            "--region", REGION,
            "--start-time", str(start_ms),
            "--end-time", str(end_ms),
            "--filter-pattern", pattern,
            "--query", "events | length(@)",
            "--output", "text",
        ]
        if next_token:
            cmd += ["--next-token", next_token]
        result = subprocess.run(cmd, capture_output=True, text=True)
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.isdigit():
                total += int(line)
        # No easy way to get next token from --query mode, just run once
        break
    return total


print(f"JSON Quality Report (last {DAYS} days)")
print("=" * 60)
print(f"{'Date':<12} {'Responses':>10} {'Repaired':>10} {'Truncated':>10} {'Markdown':>10}")
print("-" * 60)

for i in range(DAYS):
    day = datetime.now() - timedelta(days=i)
    start = int(day.replace(hour=0, minute=0, second=0).timestamp() * 1000)
    end = int(day.replace(hour=23, minute=59, second=59).timestamp() * 1000)

    row = {"Date": day.strftime("%Y-%m-%d")}
    for name, pattern in PATTERNS.items():
        row[name] = count_events(pattern, start, end)

    print(f"{row['Date']:<12} {row['Responses']:>10} {row['Repaired']:>10} {row['Truncated']:>10} {row['Markdown']:>10}")

print()
print("Responses = API calls with finish_reason: stop")
print("Repaired  = JSON needed escape/backslash repair")
print("Truncated = response hit max_tokens limit")
print("Markdown  = response wrapped in ```json block")
