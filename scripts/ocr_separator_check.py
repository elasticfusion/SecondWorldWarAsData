#!/usr/bin/env python3
"""Standalone OCR separator-vs-pagecount integrity check.

Reports, for a book's merged OCR output, how many physical pages the paginated
chunk markdown represents vs the PDF page count, and lists pages with no content
(usually blank scans). Run before trusting merged markdown that feeds the OOB
parsers / extraction. See src/ingestion/separator_check.

Usage:
  python3 scripts/ocr_separator_check.py s3://bucket/source/Book.pdf [--region us-east-1]
  python3 scripts/ocr_separator_check.py --book Book --bucket dev-wwii-data-pipeline
"""

from __future__ import annotations

import argparse
import os
import sys

import boto3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.separator_check import check_separators  # noqa: E402


def _collect_chunks(s3, bucket: str, book: str):
    """Return [(chunk_dir, markdown)] for a book's ocr-output chunks."""
    prefix = f"ocr-output/{book}/"
    chunks = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/input/input.md"):
                chunk_dir = obj["Key"][len(prefix) :].split("/", 1)[0]
                body = (
                    s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"]
                    .read()
                    .decode("utf-8", errors="replace")
                )
                chunks.append((chunk_dir, body))
    return chunks


def _pdf_page_count(s3, bucket: str, book: str) -> int:
    """Page count from the source PDF (pypdf). 0 if not found/unreadable."""
    import tempfile

    import pypdf

    for key in (f"source/{book}.pdf", f"contentrepository/{book}.pdf"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                s3.download_fileobj(bucket, key, tmp)
                tmp.flush()
                return len(pypdf.PdfReader(tmp.name).pages)
        except Exception:  # noqa: BLE001 - try next candidate
            continue
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="OCR separator vs page-count check")
    ap.add_argument("s3_path", nargs="?", help="s3://bucket/source/Book.pdf")
    ap.add_argument("--book", help="book name (with --bucket) instead of s3_path")
    ap.add_argument("--bucket", default="dev-wwii-data-pipeline")
    ap.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    ap.add_argument("--pdf-pages", type=int, default=0, help="override PDF page count")
    args = ap.parse_args()

    if args.s3_path:
        rest = args.s3_path[len("s3://") :]
        bucket, key = rest.split("/", 1)
        book = key.rsplit("/", 1)[-1].replace(".pdf", "")
    elif args.book:
        bucket, book = args.bucket, args.book
    else:
        ap.error("provide an s3://.../Book.pdf path or --book NAME")

    s3 = boto3.client("s3", region_name=args.region)
    chunks = _collect_chunks(s3, bucket, book)
    if not chunks:
        print(f"No OCR chunk markdown under ocr-output/{book}/ — nothing to check")
        return 1
    pdf_pages = args.pdf_pages or _pdf_page_count(s3, bucket, book)
    report = check_separators(chunks, pdf_pages)
    print(f"Book: {book}  (chunks: {len(chunks)})")
    print(report.summary())
    return 0 if report.ok else 2


if __name__ == "__main__":
    sys.exit(main())
