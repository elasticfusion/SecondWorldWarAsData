#!/usr/bin/env python3
"""Phase 0 (convert): promote a binary text document (EPUB/DOCX) to the chapter
structure the parse phase consumes.

Raw EPUB/DOCX can't be parsed directly (binary) and Lambda has no pandoc, so the
trigger routes these keys to this ECS task. It downloads the source, converts to
Markdown via pandoc (`src.ingestion.text_converters.convert_to_markdown`), and
writes the SAME single-chapter structure the OCR-merge path produces:

    contentrepository/{book}/chapter1/chapter1-meta.yaml     (written first)
    contentrepository/{book}/chapter1/chapter1-content.md

The `chapter1-content.md` ObjectCreated event then fires the existing parse path
(identical to OCR output), so EPUB/DOCX join the same parse -> extract -> enrich
lifecycle. Self-contained (own S3 I/O); invoked as `python phase0_convert.py`
with the target key in the CONVERT_KEY env var (set by the trigger's task
override).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import boto3

from src.ingestion.text_converters import (
    ConverterError,
    ConverterUnavailable,
    convert_to_markdown,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("phase0_convert")

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
BUCKET = os.getenv("S3_BUCKET", "")

# Extension -> the media_type token convert_to_markdown expects.
_EXT_MEDIA = {".epub": "epub", ".docx": "docx", ".txt": "text", ".text": "text"}


def _book_from_key(key: str) -> str:
    """Derive the book name from the source key (filename stem, spaces->_)."""
    stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem.replace(" ", "_")


def convert_key(key: str) -> str:
    """Convert one EPUB/DOCX/TXT S3 key to the chapter structure. Returns the
    content output key, or "" on skip/failure (logged, non-fatal)."""
    if not BUCKET:
        raise RuntimeError("S3_BUCKET env var not configured")
    ext = Path(key).suffix.lower()
    media = _EXT_MEDIA.get(ext)
    if not media:
        logger.warning("Unsupported convert extension %s (%s) — skipping", ext, key)
        return ""

    s3 = boto3.client("s3", region_name=REGION)
    book = _book_from_key(key)
    local = None
    try:
        fd, local = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        s3.download_file(BUCKET, key, local)
        # Reject a misassigned file (declared extension != actual content) BEFORE
        # feeding it to pandoc — a .epub that is really a .zip/.txt would produce
        # garbage. Off-ramp to needs-review + alert instead of processing.
        from src.ingestion.media_detection import detect_media_mismatch

        verdict = detect_media_mismatch(Path(local))
        if verdict.is_mismatch:
            logger.error(
                "REJECT %s: media-type mismatch — %s. Not converting; needs-review.",
                key,
                verdict.reason,
            )
            from src.ingestion.review_reject import reject_to_review

            reject_to_review(
                s3,
                BUCKET,
                key,
                book,
                verdict.reason,
                category="media-mismatch",
                region=REGION,
            )
            return ""
        markdown = convert_to_markdown(Path(local), media)
    except (ConverterUnavailable, ConverterError) as exc:
        logger.error("Convert failed for %s (%s)", key, exc)
        return ""
    finally:
        if local and os.path.exists(local):
            try:
                os.remove(local)
            except OSError:
                pass

    # Write meta FIRST, then content — the content-upload event triggers parse,
    # and phase1 discovery needs the meta already present (mirrors ocr_merge).
    meta_key = f"contentrepository/{book}/chapter1/chapter1-meta.yaml"
    content_key = f"contentrepository/{book}/chapter1/chapter1-content.md"
    meta = (
        'series: "TODO - Add series name"\n'
        f'book: "{book}"\n'
        'author: "TODO - Add author name"\n'
        'chapter_number: "1"\n'
        'chapter_title: "TODO - Add chapter/paper title"\n'
        'license: "TODO - Add license"\n'
        'copyright_date: "TODO - Add year"\n'
        f'source_url: "converted from {key}"\n'
    )
    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=meta.encode("utf-8"))
    s3.put_object(Bucket=BUCKET, Key=content_key, Body=markdown.encode("utf-8"))
    logger.info("Converted %s -> %s (%d chars)", key, content_key, len(markdown))
    return content_key


def main() -> int:
    """Entry: convert the CONVERT_KEY document to the chapter structure."""
    key = os.getenv("CONVERT_KEY", "")
    if not key:
        logger.error("CONVERT_KEY env var not set — nothing to convert")
        return 2
    out = convert_key(key)
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
