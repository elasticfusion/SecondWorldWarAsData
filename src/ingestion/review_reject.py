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

logger = logging.getLogger(__name__)


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
    """
    region = region or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    marker_key = f"needs-review/{category}/{book}.json"
    body = json.dumps(
        {
            "source_key": key,
            "book": book,
            "reason": reason,
            "category": category,
            "disposition": f"rejected-{category}",
        }
    )
    logger.warning("REJECT-TO-REVIEW [%s] %s: %s", category, key, reason)
    try:
        s3_client.put_object(Bucket=bucket, Key=marker_key, Body=body.encode("utf-8"))
    except Exception as e:  # pragma: no cover - best-effort marker
        logger.warning("Could not write needs-review marker for %s: %s", key, e)

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
