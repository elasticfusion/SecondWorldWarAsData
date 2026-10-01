"""OCR -> parse handoff (UNATTENDED_READINESS #1).

When a Chandra OCR Batch job SUCCEEDS, its markdown lands at
`ocr-output/{book}/[chunk-*/]input/input.md` — but nothing promotes it to
`contentrepository/`, so the parse phase never triggers and raw PDFs dead-end
after OCR (verified 2026-09-28).

This Lambda is invoked by an EventBridge rule on AWS Batch job-state-change
(status SUCCEEDED, chandra job queue). It merges the OCR markdown into
`contentrepository/{book}/{book}.md`, whose ObjectCreated event then triggers the
existing parse path via the S3 notification. Self-contained (no heavy imports);
mirrors submit_ocr_job.merge_outputs but handles the whole-PDF (no-chunk) case.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

REGION = os.getenv("AWS_REGION", "us-east-1")
# Match the convention used by every other handler (empty default + guard) so a
# missing S3_BUCKET fails loud rather than silently writing to a wrong bucket.
BUCKET = os.environ.get("S3_BUCKET", "")
CHANDRA_QUEUE_SUFFIX = "chandra"


def _s3():
    return boto3.client("s3", region_name=REGION)


def _book_from_event(detail: dict) -> str:
    """Extract the book/pdf name from a Batch job-state-change event.

    jobName is 'chandra-{book}' (set by the trigger's _submit_ocr). Prefer the
    job's S3 input path if present, else parse the jobName.
    """
    job_name = detail.get("jobName", "")
    # Try the container command's input path first (most reliable). Handle any
    # OCR-able input extension (PDF or an image — Chandra OCRs scanned maps/plates
    # too), not just .pdf.
    _ocr_exts = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp")
    cmd = (detail.get("container", {}) or {}).get("command", []) or []
    for arg in cmd:
        low = arg.lower()
        if arg.startswith("s3://") and low.endswith(_ocr_exts):
            fname = arg.rsplit("/", 1)[-1]
            return fname.rsplit(".", 1)[0]  # strip extension
    if job_name.startswith("chandra-"):
        return job_name[len("chandra-") :]
    return ""


def merge_ocr_output(book: str) -> str:
    """Merge ocr-output/{book} markdown -> contentrepository/{book}/{book}.md.

    Collects all `.../input/input.md` under the book prefix (handles both the
    chunked `ocr-output/{book}/chunk-*/input/input.md` and the whole-PDF
    `ocr-output/{book}/input/input.md` layouts), concatenates in key order, and
    writes the promoted markdown. Returns the output key.
    """
    s3 = _s3()
    if not BUCKET:
        logger.error("S3_BUCKET not set — cannot promote OCR output for %s", book)
        raise RuntimeError("S3_BUCKET env var not configured")
    prefix = f"ocr-output/{book}/"
    # A whole-PDF OCR is promoted as a single-chapter book in the structure phase1's
    # discovery requires: {book}/{chapter}/chapter*-content.md (A1). A flat
    # {book}/{book}.md yields "0 chapters" in discover_content_structure and never
    # parses. Still under contentrepository/ so the S3 trigger fires the parse path.
    output_key = f"contentrepository/{book}/chapter1/chapter1-content.md"

    md_keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/input/input.md"):
                md_keys.append(obj["Key"])
    md_keys.sort()

    if not md_keys:
        # OCR produced no markdown at all — do NOT silently drop. Off-ramp to
        # needs-review (e.g. a photo/map sent to OCR, a blank scan, or a job that
        # wrote nothing) for more extensive evaluation.
        from src.ingestion.review_reject import reject_to_review

        reject_to_review(
            s3,
            BUCKET,
            prefix,
            book,
            "OCR produced no markdown output (empty result)",
            category="ocr-empty",
            region=REGION,
        )
        return ""

    parts = []
    for k in md_keys:
        body = s3.get_object(Bucket=BUCKET, Key=k)["Body"].read().decode("utf-8")
        parts.append(body)
    merged = "\n\n".join(parts)

    # Near-empty OCR = effectively failed (blank scan, non-text image, or a
    # born-digital PDF Chandra couldn't read). Promoting it would feed garbage to
    # parse/extract. Off-ramp to needs-review instead. Threshold is deliberately
    # low (whitespace-stripped) so a legitimately short page still passes.
    min_chars = int(os.environ.get("OCR_MIN_USABLE_CHARS", "40"))
    if len(merged.strip()) < min_chars:
        from src.ingestion.review_reject import reject_to_review

        reject_to_review(
            s3,
            BUCKET,
            prefix,
            book,
            f"OCR output near-empty ({len(merged.strip())} usable chars < "
            f"{min_chars}) — likely a non-text image or unreadable scan",
            category="ocr-near-empty",
            region=REGION,
        )
        return ""
    # Write the meta FIRST, then content — the content-upload S3 event triggers the
    # parse path, and phase1's discovery needs the meta already present.
    meta_key = f"contentrepository/{book}/chapter1/chapter1-meta.yaml"
    meta = (
        f'series: "TODO - Add series name"\n'
        f'book: "{book}"\n'
        f'author: "TODO - Add author name"\n'
        f'chapter_number: "1"\n'
        f'chapter_title: "TODO - Add chapter/paper title"\n'
        f'license: "TODO - Add license"\n'
        f'copyright_date: "TODO - Add year"\n'
        f'source_url: "OCR of {book}"\n'
    )
    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=meta.encode("utf-8"))
    s3.put_object(Bucket=BUCKET, Key=output_key, Body=merged.encode("utf-8"))
    logger.info(
        "OCR->parse handoff: merged %d md file(s) -> s3://%s/%s (+meta) (%d chars)",
        len(md_keys),
        BUCKET,
        output_key,
        len(merged),
    )
    return output_key


def handler(event, _context):
    """EventBridge Batch job-state-change (SUCCEEDED) -> promote OCR output."""
    detail = event.get("detail", {})
    status = detail.get("status", "")
    if status != "SUCCEEDED":
        logger.info("Ignoring Batch event status=%s", status)
        return {"action": "none", "reason": f"status {status}"}

    # Only act on Chandra OCR jobs.
    job_queue = detail.get("jobQueue", "")
    if CHANDRA_QUEUE_SUFFIX not in job_queue:
        logger.info("Ignoring non-chandra job queue: %s", job_queue)
        return {"action": "none", "reason": "not chandra"}

    book = _book_from_event(detail)
    if not book:
        logger.error("Could not determine book from event: %s", detail.get("jobName"))
        return {"action": "error", "reason": "no book"}

    out = merge_ocr_output(book)
    if not out:
        return {"action": "none", "reason": "no ocr markdown", "book": book}
    return {"action": "promoted", "book": book, "output_key": out}
