"""Shared reject-to-review off-ramp for the ingestion front door.

Extension-based routing is a fast *guess* (pdf/image -> OCR, epub/docx -> convert,
mp4 -> video, md -> parse). When a route's guess turns out wrong — the declared
extension contradicts the file's bytes, or a processor produces no usable output —
the file must NOT be silently dropped or fed downstream as garbage. Instead it is
off-ramped to ``needs-review/`` (deliberately NOT under ``contentrepository/`` so
it never re-triggers parse) with an operator alert, for more extensive evaluation
(disposition classification, a different converter, the vision branch, or human
triage).

Used by the trigger (OCR intake), ocr_merge_handler (empty/near-empty OCR), and
phase0_convert (media-type mismatch) so every off-ramp is identical and auditable.
"""

from __future__ import annotations

import json
import logging
import os

import boto3

from src.ingestion.book_slug import _SAFE_SLUG_RE

logger = logging.getLogger(__name__)


def _sanitize_segment(value: str, fallback: str) -> str:
    """SAFE-token sanitize a value that becomes an S3 KEY PATH SEGMENT
    (``needs-review/{category}/{book}.json``). ``book`` and ``category`` are both
    attacker-influenceable (book derives from an uploaded filename; category is a
    caller literal but validated for defense-in-depth), so reject anything outside
    ``[A-Za-z0-9._-]`` — a '/' or '..' here would write the marker OUTSIDE the
    needs-review prefix / traverse the key space. Fall back to a safe constant."""
    v = (value or "").strip().replace(" ", "_")
    if v in ("", ".", "..") or not _SAFE_SLUG_RE.fullmatch(v):
        logger.warning(
            "OBSERV review.segment_rejected value=%r -> %r (unsafe for S3 key path)",
            value,
            fallback,
        )
        return fallback
    return v


def reject_to_review(
    s3_client,
    bucket: str,
    key: str,
    book: str,
    reason: str,
    *,
    category: str = "intake",
    region: str = "",
) -> str:
    """Off-ramp a file to needs-review + alert. Returns the marker S3 key.

    Writes ``needs-review/{category}/{book}.json`` (NOT under contentrepository/,
    so it never triggers parse) and publishes an operator alert to the
    phase2-complete SNS topic (email + Slack). Best-effort: marker/alert failures
    are logged, not raised — rejecting must never itself crash the handler.

    SECURITY (P1-S4/P1-C2): ``book`` and ``category`` become S3 KEY PATH SEGMENTS,
    so both are SAFE-token sanitized here (the same ``[A-Za-z0-9._-]`` rule as
    book_slug) — a crafted ``book`` like ``../../evil`` cannot escape the
    needs-review prefix. The ORIGINAL (unsanitized) book/category are preserved in
    the marker BODY for auditability.
    """
    region = region or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    safe_book = _sanitize_segment(book, "unknown")
    safe_category = _sanitize_segment(category, "intake")
    marker_key = f"needs-review/{safe_category}/{safe_book}.json"
    body = json.dumps(
        {
            "source_key": key,
            "book": book,
            "category": category,
            "safe_book": safe_book,
            "safe_category": safe_category,
            "reason": reason,
            "disposition": f"rejected-{safe_category}",
        }
    )
    logger.warning("REJECT-TO-REVIEW [%s] %s: %s", category, key, reason)
    logger.warning(
        "OBSERV review.queued category=%s book=%s key=%s reason=%s",
        category,
        book,
        key,
        str(reason)[:160],
    )
    try:
        s3_client.put_object(Bucket=bucket, Key=marker_key, Body=body.encode("utf-8"))
    except Exception as e:  # pragma: no cover - best-effort marker
        logger.warning("Could not write needs-review marker for %s: %s", key, e)
        logger.warning(
            "OBSERV review.queue_failed category=%s key=%s detail=%s "
            "(reject NOT durably recorded)",
            category,
            key,
            str(e)[:120],
        )

    topic = os.getenv("NOTIFICATION_TOPIC_ARN", "")
    if topic:
        try:
            boto3.client("sns", region_name=region).publish(
                TopicArn=topic,
                Subject=f"WWII Pipeline: {category} rejected to review",
                Message=(
                    f"Rejected {key} — {reason}. A needs-review marker was written "
                    f"to s3://{bucket}/{marker_key}. The file needs more extensive "
                    f"evaluation (verify its true type / disposition and re-route)."
                ),
            )
        except Exception as e:  # pragma: no cover - best-effort alert
            logger.warning("Could not alert on reject for %s: %s", key, e)
    return marker_key
