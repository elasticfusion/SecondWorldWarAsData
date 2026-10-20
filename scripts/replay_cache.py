#!/usr/bin/env python3
"""Download a book's synced Grok api-cache from S3 for offline replay/diagnosis.

The pipeline persists the Grok api-cache (batch requests + the per-type cached request/response
entries) to ``s3://<bucket>/diagnostics/cache/<book>/`` at the end of each Phase 2/3 run
(ecs_entrypoint._final_sync). This script pulls that back to a local dir so the extractors can be
re-run WITHOUT calling Grok (every request is a cache hit) — e.g. to reproduce an optional-entity
extract-vs-persist gap post-hoc, which previously required code-level repro because the cache died
with the container.

Usage:
  python scripts/replay_cache.py --book XChannel [--bucket dev-wwii-data-pipeline] [--dest cache/api]
Then point a GrokClient at --dest (cache_dir) and re-run the extractor offline.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _safe_book(book: str) -> str:
    b = (book or "").strip()
    if (
        not b
        or ".." in b
        or "/" in b
        or "\\" in b
        or not re.fullmatch(r"[A-Za-z0-9._-]+", b)
    ):
        raise SystemExit(f"unsafe/invalid book name: {book!r}")
    return b


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Download a book's synced Grok api-cache for replay"
    )
    ap.add_argument("--book", required=True, help="book name (e.g. XChannel)")
    ap.add_argument(
        "--bucket", default=os.getenv("S3_BUCKET", "dev-wwii-data-pipeline")
    )
    ap.add_argument("--dest", default="cache/api", help="local cache dir to populate")
    ap.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    args = ap.parse_args()

    book = _safe_book(args.book)
    import boto3

    s3 = boto3.client("s3", region_name=args.region)
    prefix = f"diagnostics/cache/{book}/"
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    n = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=args.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel = key[len(prefix) :]
            if not rel:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(args.bucket, key, str(target))
            n += 1
    if n == 0:
        print(f"No synced cache found at s3://{args.bucket}/{prefix}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Downloaded {n} cache file(s) from s3://{args.bucket}/{prefix} -> {dest}")
    print(
        f"Replay: point a GrokClient at cache_dir={dest} and re-run the extractor offline."
    )


if __name__ == "__main__":
    main()
