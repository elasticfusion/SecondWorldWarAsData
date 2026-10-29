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

import yaml

from src.ingestion.book_slug import book_slug
from src.utils.observe import observe_error

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

REGION = os.getenv("AWS_REGION", "us-east-1")
# Match the convention used by every other handler (empty default + guard) so a
# missing S3_BUCKET fails loud rather than silently writing to a wrong bucket.
BUCKET = os.environ.get("S3_BUCKET", "")
CHANDRA_QUEUE_SUFFIX = "chandra"
ENV_NAME = os.getenv("ENV_NAME", "dev")
CLUSTER = os.getenv("ECS_CLUSTER", f"{ENV_NAME}-wwii-pipeline")
PHASE0_INGEST_TASK_DEF = os.getenv(
    "PHASE0_INGEST_TASK_DEF", f"{ENV_NAME}-wwii-phase0-ingest"
)


def _is_structured(s3, book: str) -> bool:
    """True if the trigger tagged this book structured-reference (OOB track):
    an ``ocr-output/{book}/.structured`` sidecar marker exists."""
    try:
        s3.head_object(Bucket=BUCKET, Key=f"ocr-output/{book}/.structured")
        return True
    except Exception:  # noqa: BLE001 - absent marker (404) = not structured
        return False


def _route_to_oob(s3, book: str, merged: str) -> str:
    """Structured-reference routing: place the OCR markdown where the OOB parser
    (phase0_ingest.discover_oob_markdown) consumes it (``{book}/ocr_output/*.md``)
    and launch the phase0-ingest ECS task. Returns the OOB input key on a confirmed
    launch, or ``""`` if the phase0-ingest task FAILED to launch (#44: a launch
    failure must NOT masquerade as a successful promote — the OOB markdown is in S3
    but nothing will parse it, so the caller must see the drop). Does NOT promote to
    contentrepository/ (so it never enters narrative parse)."""
    oob_key = f"contentrepository/{book}/ocr_output/{book}.md"
    s3.put_object(Bucket=BUCKET, Key=oob_key, Body=merged.encode("utf-8"))
    logger.info(
        "STRUCTURED route: OCR markdown -> s3://%s/%s (OOB parser track)",
        BUCKET,
        oob_key,
    )
    # #44: a launch failure must NOT return a success key — the OOB markdown is in S3
    # but nothing will parse it. _launch_phase0_ingest emits the canonical OBSERV at the
    # point of loss and returns False, so we surface the drop instead of a bogus promote.
    if not _launch_phase0_ingest(book):
        return ""
    return oob_key


def _launch_phase0_ingest(book: str) -> bool:
    """Launch the phase0-ingest (OOB parser) ECS task for ``book``. Returns True on a
    confirmed launch, False on ANY launch failure (exception OR an ECS placement that
    placed no task). On failure emits a canonical, greppable, Slack-escalating
    ``OBSERV ocr_merge.oob_launch_error status=error`` at the point of loss (#44) so an
    OOB book that will never parse is loud, not a silent success. Never raises."""
    try:
        subnets = [x for x in os.getenv("PRIVATE_SUBNET_IDS", "").split(",") if x]
        sg = os.getenv("SECURITY_GROUP_ID", "")
        resp = (
            boto3.client("ecs", region_name=REGION).run_task(
                cluster=CLUSTER,
                taskDefinition=PHASE0_INGEST_TASK_DEF,
                count=1,
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": subnets,
                        "securityGroups": [sg] if sg else [],
                        "assignPublicIp": "DISABLED",
                    }
                },
                overrides={
                    "containerOverrides": [
                        {
                            "name": "pipeline",
                            "environment": [{"name": "BOOK_NAME", "value": book}],
                        }
                    ]
                },
            )
            or {}
        )
    except Exception as e:  # noqa: BLE001 - surface loud, don't crash the merge
        observe_error(
            "ocr_merge",
            "oob_launch_error",
            note="phase0-ingest launch raised (OOB book will NOT parse)",
            book=book,
            reason=type(e).__name__,
        )
        logger.error("Failed to launch phase0-ingest for %s: %s", book, e)
        return False
    if not resp.get("tasks") or resp.get("failures"):
        # ECS placement failure (capacity/quota) — run_task does NOT raise.
        observe_error(
            "ocr_merge",
            "oob_launch_error",
            note="phase0-ingest task placed no task (OOB book will NOT parse)",
            book=book,
            reason="no_task_placed",
            failures=resp.get("failures"),
        )
        logger.error(
            "Failed to launch phase0-ingest for %s (no task placed, failures=%s)",
            book,
            resp.get("failures"),
        )
        return False
    logger.info("Launched phase0-ingest (OOB parser) for %s", book)
    return True


def _s3():
    return boto3.client("s3", region_name=REGION)


def _book_from_event(detail: dict) -> str:
    """Extract the book/pdf name from a Batch job-state-change event, converged on
    the shared SAFE slug (P1-S4/P1-C2).

    jobName is 'chandra-{book}' (set by the trigger's _submit_ocr). Prefer the
    job's S3 input path if present, else parse the jobName. Either candidate is
    run through :func:`src.ingestion.book_slug.book_slug` (filename stem,
    spaces->_, ``[A-Za-z0-9._-]+`` or loud 'unknown') so the OCR-merge derivation
    matches the convert / video paths and a crafted jobName/path cannot inject a
    traversal or unsafe char into the S3/review keys it builds.
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
            # book_slug strips the path + extension and validates the stem.
            return book_slug(arg)
    if job_name.startswith("chandra-"):
        # Validate the jobName-derived stem the same way (no extension to strip).
        return book_slug(job_name[len("chandra-") :])
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
    # STRUCTURED-REFERENCE fork: if the trigger tagged this doc structured (ETO
    # OOB etc.), its OCR markdown goes to the deterministic OOB parser track, NOT
    # narrative parse. Write it to the ocr_output/ layout phase0_ingest consumes
    # and launch the phase0-ingest task instead of promoting to contentrepository/.
    if _is_structured(s3, book):
        return _route_to_oob(s3, book, merged)
    # Narrative track: normalize to English before promote-to-parse. Chandra emits
    # physical-page separators, so detect/translate PER PAGE (a scanned roll can
    # mix languages). Fail-safe: English/disabled/error -> unchanged. (The OOB
    # fork above translates in phase0_ingest, so it is intentionally skipped here.)
    from src.ingestion.translation import normalize_markdown_to_english

    merged = normalize_markdown_to_english(merged, per_page=True)
    meta_key = f"contentrepository/{book}/chapter1/chapter1-meta.yaml"
    # P2-C1: SAFE YAML via yaml.safe_dump (never an f-string) so a book name with
    # YAML metacharacters cannot inject structure. author/series/chapter_title/
    # license are '' (empty) NOT a literal 'TODO - Add ...'; the gap is recorded
    # as a needs-review/metadata marker below so it is detectable, not masqueraded.
    meta = {
        "series": "",
        "book": book,
        "author": "",
        "chapter_number": "1",
        "chapter_title": "",
        "license": "",
        "copyright_date": "",
        "source_url": f"OCR of {book}",
    }
    meta_body = yaml.safe_dump(
        meta, default_flow_style=False, allow_unicode=True
    ).encode("utf-8")
    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=meta_body)
    s3.put_object(Bucket=BUCKET, Key=output_key, Body=merged.encode("utf-8"))
    _mark_metadata_gap(s3, prefix, book)
    logger.info(
        "OCR->parse handoff: merged %d md file(s) -> s3://%s/%s (+meta) (%d chars)",
        len(md_keys),
        BUCKET,
        output_key,
        len(merged),
    )
    return output_key


def _mark_metadata_gap(s3, key: str, book: str) -> None:
    """Record the deferred-metadata gap (empty author/series/title/license) as a
    detectable needs-review/metadata marker (P2-C1), rather than masquerading
    empty fields as real data. Fail-safe: best-effort, never breaks the merge."""
    try:
        from src.ingestion.review_reject import reject_to_review

        reject_to_review(
            s3,
            BUCKET,
            key,
            book,
            "metadata not yet extracted (author/series/title/license empty) — "
            "real metadata extraction deferred (P2-C1 slug+meta half)",
            category="metadata",
            region=REGION,
        )
    except Exception as e:  # noqa: BLE001 - marker must never crash the merge
        logger.warning("metadata needs-review marker failed for %s: %s", key, e)


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
        # #11: a paid Chandra OCR job SUCCEEDED but we cannot derive its book from the
        # event — the OCR output would dead-end silently. Emit a canonical, greppable,
        # Slack-escalating anomaly at the point of loss (not a generic-only error).
        job_name = detail.get("jobName")
        observe_error(
            "ocr_merge",
            "book_underivable",
            note="Chandra OCR SUCCEEDED but book undetectable — paid OCR output will dead-end",
            job_name=job_name,
            job_queue=job_queue,
        )
        logger.error("Could not determine book from event: %s", job_name)
        return {"action": "error", "reason": "no book"}

    out = merge_ocr_output(book)
    if not out:
        return {"action": "none", "reason": "no ocr markdown", "book": book}
    return {"action": "promoted", "book": book, "output_key": out}
