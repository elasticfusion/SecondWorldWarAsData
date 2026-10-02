"""Tag consumed source binaries for cold-archive lifecycle transition.

Large source media/documents (video, scanned PDFs, large images) are read ONLY
during ingestion (OCR / convert / transcribe) and then kept purely for citation /
re-processing. Once a track has produced its chapter output, the source is
terminal and should drop to cheap cold storage.

Mechanism: tag the object ``archive=cold``; a tag-based S3 lifecycle rule
(storage.yaml ``ColdArchiveTaggedToGlacierIR``) transitions tagged objects to
Glacier Instant Retrieval. Tag-based (not prefix/size) because the raw media live
in unpredictable per-title dirs mixed with active markdown, and the template's
lifecycle schema cannot size-filter. Glacier IR (not deeper) keeps the object
transparently readable for an auto-triggered re-processing (no restore).

Fail-safe: tagging never raises — a missed tag only means the object stays on
STANDARD (a cost nit), never a pipeline failure.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ARCHIVE_TAG_KEY = "archive"
ARCHIVE_TAG_VALUE = "cold"


def tag_source_cold(s3_client, bucket: str, key: str) -> None:
    """Tag a consumed source object ``archive=cold`` so the lifecycle rule moves
    it to Glacier IR. Best-effort: logs and swallows any error."""
    try:
        s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={"TagSet": [{"Key": ARCHIVE_TAG_KEY, "Value": ARCHIVE_TAG_VALUE}]},
        )
        logger.info(
            "Tagged source %s %s=%s (cold archive)",
            key,
            ARCHIVE_TAG_KEY,
            ARCHIVE_TAG_VALUE,
        )
    except Exception as e:  # noqa: BLE001 - cost optimization, never block ingestion
        logger.warning("Could not tag %s for cold archive: %s", key, e)
