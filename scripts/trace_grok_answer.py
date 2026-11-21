#!/usr/bin/env python3
"""Which batch answered a cached Grok answer? (task 1.7: provenance of Phase 3 answers)

    python3 scripts/trace_grok_answer.py <request_id> [--metrics-dir output/metrics]

Prints the batch (id, name, round, submitted, answering model) of every batch that submitted the
request. Download the metrics first when they are only in S3:
    aws s3 sync s3://<bucket>/output/metrics/ output/metrics/
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.utils.answer_provenance import batches_for


def _when(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "unknown"
    except (OverflowError, OSError, ValueError):
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("request_id")
    ap.add_argument("--metrics-dir", default="output/metrics", type=Path)
    args = ap.parse_args(argv)
    if not args.metrics_dir.is_dir():
        print(f"No metrics directory at {args.metrics_dir}", file=sys.stderr)
        return 2
    found = batches_for(args.request_id, args.metrics_dir)
    if not found:
        print(f"No batch in {args.metrics_dir} submitted request {args.request_id}.")
        return 1
    for b in found:
        when = _when(b["submitted_at"])
        models = (
            ", ".join(f"{m} x{n}" for m, n in b["served_models"].items()) or "unknown"
        )
        print(
            f"batch {b['batch_id']}  round {b['round'] or '?'}  kind {b['cache_type']}  "
            f"submitted {when}  answered by {models}  ({b['batch_name']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
