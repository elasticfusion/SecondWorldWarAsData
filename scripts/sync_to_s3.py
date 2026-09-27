#!/usr/bin/env python3
"""Sync local source docs into S3 contentrepository/ (pre-stage input step).

The concurrency pipeline's pre-stage (M1) assumes docs are reachable in S3; this
is the step that gets them there. Deliberately SCOPED — the full WWIIArchives is
~502 GB, so you sync a chosen subset by pointing --source at a subdir. Idempotent
(wraps `aws s3 sync`, which transfers only changed files), with a --dry-run that
previews the transfer + total size before any upload.

Examples:
  # Preview what a subset would upload (no transfer):
  python3 scripts/sync_to_s3.py --source "~/Downloads/WWIIArchives/FMS/B-Series/B 400-499" \\
      --dest "FMS/B-Series/B 400-499" --dry-run

  # Actually sync it:
  python3 scripts/sync_to_s3.py --source "~/Downloads/WWIIArchives/Maps" --dest "Maps"

Safety: refuses to sync a source larger than --max-gb (default 50) unless --force,
so a stray point at the 502 GB root can't kick off a massive transfer by accident.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("sync_to_s3")

DEFAULT_BUCKET = os.getenv("S3_BUCKET", "dev-wwii-data-pipeline")
DEFAULT_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
CONTENT_PREFIX = "contentrepository"


def dir_size_bytes(path: Path) -> int:
    """Total size of files under path (bytes)."""
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def build_sync_command(
    source: Path, bucket: str, dest: str, region: str, dry_run: bool
) -> list:
    """Build the `aws s3 sync` argv. dest is placed under contentrepository/."""
    s3_uri = f"s3://{bucket}/{CONTENT_PREFIX}/{dest.strip('/')}/"
    cmd = [
        "aws",
        "s3",
        "sync",
        str(source),
        s3_uri,
        "--region",
        region,
        # Skip junk that shouldn't reach the corpus.
        "--exclude",
        ".DS_Store",
        "--exclude",
        "*/.git/*",
    ]
    if dry_run:
        cmd.append("--dryrun")
    return cmd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Sync local docs into S3 contentrepository/"
    )
    ap.add_argument("--source", required=True, type=Path, help="Local source directory")
    ap.add_argument(
        "--dest",
        required=True,
        help="Destination subpath under contentrepository/ (e.g. 'FMS/B-Series/B 400-499')",
    )
    ap.add_argument("--bucket", default=DEFAULT_BUCKET)
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument(
        "--dry-run", action="store_true", help="Preview only (aws s3 sync --dryrun)"
    )
    ap.add_argument(
        "--max-gb",
        type=float,
        default=50.0,
        help="Refuse to sync a source larger than this unless --force (guards against the 502GB root)",
    )
    ap.add_argument("--force", action="store_true", help="Override the --max-gb guard")
    args = ap.parse_args(argv)

    source = args.source.expanduser()
    if not source.exists() or not source.is_dir():
        logger.error("Source not found or not a directory: %s", source)
        return 2

    size_gb = dir_size_bytes(source) / (1024**3)
    logger.info("Source: %s (%.2f GB)", source, size_gb)
    logger.info(
        "Dest:   s3://%s/%s/%s/", args.bucket, CONTENT_PREFIX, args.dest.strip("/")
    )

    if size_gb > args.max_gb and not args.force:
        logger.error(
            "Source is %.1f GB > --max-gb %.1f. This looks large (the full archive is "
            "~502 GB). Re-run with --force if intentional, or point --source at a subset.",
            size_gb,
            args.max_gb,
        )
        return 3

    cmd = build_sync_command(source, args.bucket, args.dest, args.region, args.dry_run)
    logger.info("%s: %s", "DRY-RUN" if args.dry_run else "SYNC", " ".join(cmd))
    result = subprocess.run(cmd, check=False)  # nosec B603 - argv list, no shell
    if result.returncode != 0:
        logger.error("s3 sync exited %d", result.returncode)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
