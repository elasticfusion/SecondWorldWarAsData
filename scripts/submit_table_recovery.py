"""Submit PP-StructureV3 table-recovery Batch jobs for flattened pages.

This is the orchestration caller that makes the flattened-table auto-routing
*fire*: it connects Chandra's markdown output to the Paddle GPU worker
(``Dockerfile.paddle`` / ``{env}-wwii-paddle`` Batch job def).

Pre-emptive routing, done cheaply: the routing decision runs LOCALLY by scanning
each page's Chandra markdown for the flattened 2-D task-org signature
(``src/ingestion/table_recovery.page_needs_recovery``, which reuses the existing
``detect_flattened_tables`` detector). Only pages that actually flattened are
rendered, uploaded, and sent to a GPU job — so PP-StructureV3 cost is spent only
where Chandra failed, never on prose/normal-table pages.

Per flattened page it:
  1. Renders the PDF page to a 300-DPI PNG (matching the OCR render standard).
  2. Uploads the page image next to the (already-existing) per-page markdown.
  3. Submits a ``{env}-wwii-paddle`` Batch job (image + markdown -> recovery
     JSON) via ``scripts/paddle_entrypoint.sh``.

Inputs:
  * ``--pdf s3://.../source/book.pdf`` — source PDF (rendered per page).
  * ``--markdown-prefix s3://.../ocr-output/book/`` — where per-page Chandra
    markdown lives (``p<N>.md`` per physical page). One file per page is the
    contract the recovery worker consumes.

This script only *submits* (optionally ``--wait``); it does not merge or apply
recovered tables — reconciliation of Chandra vs. Paddle output is a downstream
step, by design (ensemble-as-verification).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.table_recovery import (  # pylint: disable=wrong-import-position
    RECOVERY_RENDER_DPI,
    page_needs_recovery,
)

DEFAULT_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")


def _parse_s3(path: str) -> Tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``."""
    if not path.startswith("s3://"):
        raise ValueError(f"not an s3 path: {path}")
    bucket, _, key = path[len("s3://") :].partition("/")
    return bucket, key


def _list_page_markdown(s3, bucket: str, prefix: str) -> List[Tuple[int, str]]:
    """Return ``(page_number, key)`` for each per-page markdown under a prefix.

    The recovery worker consumes one markdown file per page. This discovers
    ``.../p<N>.md`` objects and parses the 1-based page number from the stem.
    """
    pages: List[Tuple[int, str]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            stem = key.rsplit("/", 1)[-1]
            if not stem.endswith(".md") or not stem[:-3].startswith("p"):
                continue
            num = stem[1:-3]
            if num.isdigit():
                pages.append((int(num), key))
    return sorted(pages)


def _render_page_png(pdf_local: Path, page_number: int, dpi: int) -> Path:
    """Render a 1-based PDF page to a temp PNG and return its path."""
    import fitz  # local import: only when actually rendering

    zoom = dpi / 72.0
    with fitz.open(str(pdf_local)) as doc:
        pix = doc[page_number - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        out = Path(
            tempfile.mkstemp(prefix=f"recover_p{page_number}_", suffix=".png")[1]
        )
        pix.save(str(out))
    return out


def _flattened_pages(
    s3, bucket: str, pages: List[Tuple[int, str]]
) -> List[Tuple[int, str]]:
    """Filter to pages whose markdown contains a flattened task-org table."""
    hits: List[Tuple[int, str]] = []
    for page_number, key in pages:
        body = (
            s3.get_object(Bucket=bucket, Key=key)["Body"]
            .read()
            .decode("utf-8", errors="replace")
        )
        if page_needs_recovery(body):
            hits.append((page_number, key))
    return hits


def _submit_page(  # pylint: disable=too-many-arguments
    batch,
    s3,
    *,
    bucket: str,
    md_key: str,
    page_number: int,
    pdf_local: Path,
    out_prefix: str,
    job_queue: str,
    job_def: str,
    dpi: int,
) -> str:
    """Render+upload the page image and submit one Paddle recovery job."""
    png = _render_page_png(pdf_local, page_number, dpi)
    img_key = f"{out_prefix}p{page_number}.png"
    result_key = f"{out_prefix}p{page_number}.recovery.json"
    try:
        s3.upload_file(str(png), bucket, img_key)
    finally:
        png.unlink(missing_ok=True)

    job_name = f"paddle-recover-p{page_number}"[:128]
    resp = batch.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_def,
        containerOverrides={
            "command": [
                f"s3://{bucket}/{img_key}",
                f"s3://{bucket}/{md_key}",
                f"s3://{bucket}/{result_key}",
            ]
        },
    )
    print(f"  Submitted {job_name} (p{page_number}) -> {resp['jobId'][:12]}")
    return resp["jobId"]


def main(argv: Optional[List[str]] = None) -> int:
    """Discover flattened pages and submit a recovery job for each."""
    parser = argparse.ArgumentParser(
        description="Submit PP-StructureV3 table-recovery jobs for flattened pages"
    )
    parser.add_argument("--pdf", required=True, help="s3://.../source/book.pdf")
    parser.add_argument(
        "--markdown-prefix",
        required=True,
        help="s3://.../ocr-output/book/ (holds per-page pN.md)",
    )
    parser.add_argument("--env", default="dev", help="Environment name (default dev)")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--dpi", type=int, default=RECOVERY_RENDER_DPI, help="Page render DPI"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report which pages would be routed; submit nothing.",
    )
    args = parser.parse_args(argv)

    s3 = boto3.client("s3", region_name=args.region)
    src_bucket, src_key = _parse_s3(args.pdf)
    md_bucket, md_prefix = _parse_s3(args.markdown_prefix)
    if not md_prefix.endswith("/"):
        md_prefix += "/"

    pages = _list_page_markdown(s3, md_bucket, md_prefix)
    print(f"Found {len(pages)} per-page markdown file(s) under {args.markdown_prefix}")
    flattened = _flattened_pages(s3, md_bucket, pages)
    print(f"{len(flattened)} page(s) contain a flattened task-org table (routed)")

    if not flattened:
        print("Nothing to recover.")
        return 0
    if args.dry_run:
        print(
            "Dry run — pages that would be routed:",
            ", ".join(f"p{n}" for n, _ in flattened),
        )
        return 0

    # Download the source PDF once for local page rendering.
    pdf_local = Path(tempfile.mkstemp(prefix="recover_src_", suffix=".pdf")[1])
    s3.download_file(src_bucket, src_key, str(pdf_local))

    batch = boto3.client("batch", region_name=args.region)
    job_queue = f"{args.env}-wwii-chandra-gpu"  # shared GPU queue
    job_def = f"{args.env}-wwii-paddle"
    submitted: List[str] = []
    try:
        for page_number, md_key in flattened:
            submitted.append(
                _submit_page(
                    batch,
                    s3,
                    bucket=md_bucket,
                    md_key=md_key,
                    page_number=page_number,
                    pdf_local=pdf_local,
                    out_prefix=md_prefix,
                    job_queue=job_queue,
                    job_def=job_def,
                    dpi=args.dpi,
                )
            )
    finally:
        pdf_local.unlink(missing_ok=True)

    print(f"Submitted {len(submitted)} recovery job(s) to {job_queue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
