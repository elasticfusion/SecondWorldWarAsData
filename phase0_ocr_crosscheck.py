#!/usr/bin/env python3
"""Phase 0 (OCR cross-check): publish an OCR'd book whose upload carries a validation URL.

Launched by the OCR merge (lambda_handlers/ocr_merge_handler) once every OCR chunk of the
book's submission is in, with BOOK_NAME set. It reads the submission's chunk manifest
(``ocr-output/{book}/.chunks.json``: the chunks, the uploaded object and its validation URL),
then - through src/ingestion/ocr_promote - sanitises the OCR, translates non-English pages,
splits the book into chapters, cross-checks and corrects each chapter against its
transcription (src/ingestion/ocr_crosscheck), normalises them for the parser and writes
``contentrepository/{book}/chapterN/``, whose upload events start Phase 1.

The raw merge was kept by the merge (``ocr-output/{book}/merged/raw.md``). A run that fails
releases the merge claim and exits 1 with a canonical OBSERV error, so the book can be
re-driven (re-run the merge) rather than silently dead-ending.
"""

from __future__ import annotations

import logging
import os
import sys

import boto3

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("phase0_ocr_crosscheck")

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
BUCKET = os.getenv("S3_BUCKET", "")


class Superseded(RuntimeError):
    """The book was re-submitted for OCR after this task was launched: the newer submission's
    own merge publishes it."""


def _require_current(s3, bucket: str, book: str, submission: str) -> None:
    """Raise Superseded if the book was re-submitted for OCR since this task started."""
    from src.ingestion import ocr_chunk_manifest as cm

    now = cm.read_manifest(s3, bucket, book)
    if submission and now is not None and now.submission != submission:
        raise Superseded(f"{book}: re-submitted as {now.submission} during the run")


def run(s3, bucket: str, book: str, submission: str = "") -> list:
    """Publish ``book``'s chapters for its OCR ``submission``; returns the content keys."""
    from src.ingestion import ocr_chunk_manifest as cm
    from src.ingestion import ocr_promote
    from src.ingestion.translation import normalize_markdown_to_english

    manifest = cm.read_manifest(s3, bucket, book)
    if manifest is None:
        raise RuntimeError(f"no OCR chunk manifest for {book}: nothing to cross-check")
    if submission and manifest.submission != submission:
        raise Superseded(
            f"{book}: launched for {submission}, manifest is {manifest.submission}"
        )
    missing = cm.missing_chunks(s3, bucket, book, manifest)
    if missing:
        raise RuntimeError(f"OCR chunks missing for {book}: {missing[:5]}")
    keys = [cm.chunk_markdown_key(book, c) for c in manifest.chunks]
    parts = ocr_promote.read_parts(
        s3, bucket, keys, [cm.first_page(c) for c in manifest.chunks]
    )
    written = ocr_promote.promote(
        s3,
        bucket,
        book,
        parts,
        source_key=manifest.source_key or f"ocr-output/{book}/",
        validation_url=manifest.validation_url,
        still_current=lambda: _require_current(s3, bucket, book, submission),
        translate=lambda text: normalize_markdown_to_english(text, per_page=True),
    )
    return written


def main() -> int:
    """Exit 0 when the chapters are published, 1 on a failure (alerted), 2 on bad input."""
    from src.ingestion.book_slug import book_slug
    from src.utils.observe import observe_error

    book = book_slug(os.getenv("BOOK_NAME", ""))
    if not BUCKET or not book or book == "unknown":
        observe_error(
            "phase0_crosscheck",
            "bad_input",
            note="S3_BUCKET and a valid BOOK_NAME are required",
            book=os.getenv("BOOK_NAME", ""),
        )
        return 2
    from src.ingestion import ocr_chunk_manifest as cm

    submission = os.getenv("SUBMISSION_ID", "")
    s3 = boto3.client("s3", region_name=REGION)
    try:
        written = run(s3, BUCKET, book, submission)
    except Superseded as e:
        logger.info(
            "OBSERV phase0_crosscheck.superseded status=ok book=%s (%s)", book, e
        )
        return 0
    except (
        Exception
    ) as e:  # noqa: BLE001 - make the loss loud, release the claim, exit 1
        observe_error(
            "phase0_crosscheck",
            "failed",
            note=(
                "OCR cross-check/publish failed: the book is NOT parsed (the raw merge is "
                "kept). Fix the cause, then re-drive: " + cm.redrive_command(book)
            ),
            book=book,
            reason=f"{type(e).__name__}: {str(e)[:300]}",
        )
        try:
            cm.release_merge(s3, BUCKET, book, submission)
        except Exception as release_err:  # noqa: BLE001
            logger.error(
                "could not release the merge claim for %s: %s", book, release_err
            )
        return 1
    logger.info("OBSERV phase0_crosscheck.done book=%s chapters=%d", book, len(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
