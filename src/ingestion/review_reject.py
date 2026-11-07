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


_CORE_MARKER_KEYS = frozenset(
    {
        "source_key",
        "book",
        "category",
        "safe_book",
        "safe_category",
        "reason",
        "disposition",
    }
)
_SNS_SUBJECT_MAX = (
    100  # SNS rejects a longer Subject, which would silently lose the alert
)


def _sns_subject(subject: str) -> str:
    """SNS Subject must be <=100 ASCII chars with no line breaks; anything else makes publish FAIL
    (and the failure is swallowed), so the alert would be lost."""
    clean = " ".join(subject.split()).encode("ascii", "replace").decode("ascii")
    return (
        clean
        if len(clean) <= _SNS_SUBJECT_MAX
        else clean[: _SNS_SUBJECT_MAX - 3] + "..."
    )


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
    alert_subject: str = "",
    alert_message: str = "",
    extra: dict | None = None,
) -> str:
    """Off-ramp a file to needs-review + alert. Returns the marker S3 key.

    ``alert_subject`` / ``alert_message`` override the default "verify its true type and re-route"
    text, which is only right for a genuine reject (wrong media type, empty output). A caller whose
    situation needs different action (e.g. mark_metadata_gap) must say what to DO. ``extra`` adds
    fields to the marker body.

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
        | {k: v for k, v in (extra or {}).items() if k not in _CORE_MARKER_KEYS}
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
                Subject=_sns_subject(
                    alert_subject or f"WWII Pipeline: {category} rejected to review"
                ),
                Message=alert_message
                or (
                    f"Rejected {key} — {reason}. A needs-review marker was written "
                    f"to s3://{bucket}/{marker_key}. The file needs more extensive "
                    f"evaluation (verify its true type / disposition and re-route)."
                ),
            )
        except Exception as e:  # pragma: no cover - best-effort alert
            logger.warning("Could not alert on reject for %s: %s", key, e)
    return marker_key


_METADATA_GAP_NOTE = (
    "metadata not yet extracted (author/series/title/license empty) — "
    "real metadata extraction deferred (P2-C1 slug+meta half)"
)


_DEFAULT_GAP_FIELDS = ("author", "series", "chapter_title", "license", "copyright_date")


def _metadata_gap_alert(
    key: str, book: str, bucket: str, meta_key: str, fields: tuple[str, ...]
) -> tuple[str, str]:
    """(subject, message) for an empty-metadata alert: which fields, what it breaks, what to do.

    Every claim is checked against the code/E2E: the parse hashes author, so a Phase 1 re-run
    re-parses after the edit; weather logged 'Missing required book metadata' and dates/places/
    people_groups raise the same on their sync path."""
    import json as _json
    import shlex

    env = bucket.split("-wwii-")[0] if "-wwii-" in bucket else "<env>"
    subject = f"WWII Pipeline: metadata missing for {book[:45]}"
    common = (
        f"({key[:200]}) was ingested with EMPTY metadata: {', '.join(fields)}. "
        f"Processing CONTINUES, but until this is filled: mentions of this book carry an empty "
        f"author in their provenance, its licence/copyright is not recorded, and Phase 2 "
        f"extractors that require an author (weather; dates, places and people groups on their "
        f"sync path) fail with 'Missing required book metadata'. "
        f"TO FIX: edit the chapter metadata and re-run Phase 1 for the book (it re-parses because "
        f"the metadata changed) and Phase 2 follows; do NOT re-upload the source (that rewrites "
        f"the metadata empty). "
    )
    # The message embeds a copy-paste shell command, so `book` must be the safe slug — defence in
    # depth even though every caller passes book_slug output. An unsafe/unknown book => no command.
    if _SAFE_SLUG_RE.fullmatch(book or "") and book != "unknown":
        payload = _json.dumps({"source": "manual", "book": book, "phase": "1"})
        cmd = (
            f"Edit s3://{bucket}/{meta_key} (set {', '.join(fields)} + a real source_url if it is "
            f"a placeholder), then: aws lambda invoke --function-name {env}-wwii-trigger "
            f"--payload {shlex.quote(payload)} --cli-binary-format raw-in-base64-out /tmp/out.json."
        )
    else:
        cmd = (
            f"The book name could not be identified safely, so no command is given: edit the "
            f"chapter metadata under s3://{bucket}/contentrepository/<book>/ and re-run Phase 1."
        )
    return subject, f"{book} {common}{cmd} Nothing needs re-routing."


def mark_metadata_gap(
    s3_client,
    bucket: str,
    key: str,
    book: str,
    region: str,
    *,
    note: str = _METADATA_GAP_NOTE,
    fields: tuple[str, ...] = _DEFAULT_GAP_FIELDS,
    meta_key: str = "",
) -> None:
    """Record the deferred-metadata gap (empty author/series/title/license) as a detectable
    needs-review/metadata marker (P2-C1) rather than masquerading empty fields as real data.

    Shared by ocr_merge_handler / phase0_convert / phase0_video (identical off-ramp). Fail-safe:
    best-effort, never raises into the caller's merge/convert/phase.
    """
    meta_key = meta_key or f"contentrepository/{book}/chapter1/chapter1-meta.yaml"
    subject, message = _metadata_gap_alert(key, book, bucket, meta_key, fields)
    try:
        reject_to_review(
            s3_client,
            bucket,
            key,
            book,
            note,
            category="metadata",
            region=region,
            alert_subject=subject,
            alert_message=message,
            extra={"missing_fields": list(fields), "meta_key": meta_key},
        )
    except Exception as e:  # noqa: BLE001 - marker must never crash the caller
        logger.warning("metadata needs-review marker failed for %s: %s", key, e)
