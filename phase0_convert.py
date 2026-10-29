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
import yaml

from src.ingestion.book_slug import book_slug
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
_EXT_MEDIA = {
    ".epub": "epub",
    ".docx": "docx",
    ".txt": "text",
    ".text": "text",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
}


def _book_from_key(key: str) -> str:
    """Converged, SAFE book slug for the source key (P1-S4/P1-C2).

    Delegates to the single shared :func:`src.ingestion.book_slug.book_slug`
    (filename stem, spaces->_, ``[A-Za-z0-9._-]+`` or loud 'unknown' fallback) so
    this task's derivation can no longer diverge from the video / OCR-merge paths.
    """
    return book_slug(key)


def _grok_correct_fallback(local_path, book: str):
    """Second-pass recovery: read the raw file text and ask Grok to repair it
    into clean markdown. Returns corrected markdown or None (caller escalates to
    human review). Best-effort — never raises."""
    try:
        from src.ingestion.md_correction import correct_markdown
        from src.grok_client import GrokClient
        from src.utils.config import load_config, get_paths

        raw = Path(local_path).read_text(encoding="utf-8", errors="replace")
        paths = get_paths(load_config())
        client = GrokClient(paths["api_cache"])
        return correct_markdown(raw, grok_client=client, source_hint=book)
    except Exception as e:  # noqa: BLE001 - fallback must never crash the convert
        logger.warning("Grok MD-correction fallback unavailable for %s: %s", book, e)
        return None


def _download_detect_convert(s3, key: str, book: str, ext: str, media: str) -> str:
    """Download -> media-mismatch gate -> pandoc convert. Returns markdown, or
    '' when the file is off-ramped (type-spoof / media mismatch). Raises only
    (ConverterUnavailable, ConverterError) — handled by the caller's converter
    off-ramp — or an unexpected Exception the caller's broad guard off-ramps.
    """
    local = None
    try:
        fd, local = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        s3.download_file(BUCKET, key, local)
        # Reject a misassigned file (declared extension != actual content) BEFORE
        # feeding it to pandoc — a .epub that is really a .zip/.txt (or a
        # decompression bomb) would produce garbage. Off-ramp to needs-review.
        from src.ingestion.media_detection import detect_media_mismatch

        verdict = detect_media_mismatch(Path(local))
        if verdict.is_mismatch:
            logger.error(
                "REJECT %s: media-type mismatch — %s. Not converting; needs-review.",
                key,
                verdict.reason,
            )
            logger.warning(
                "OBSERV phase0.rejected reason=media_mismatch book=%s key=%s detail=%s",
                book,
                key,
                str(verdict.reason)[:120],
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
        return convert_to_markdown(Path(local), media)
    finally:
        if local and os.path.exists(local):
            try:
                os.remove(local)
            except OSError:
                pass


def _recover_or_offramp(s3, key: str, book: str, exc: Exception) -> str:
    """Converter off-ramp: a (ConverterUnavailable|ConverterError) choke -> try a
    Grok MD-correction recovery over the raw text; if that fails, reject to review
    and return ''. Returns recovered markdown on success, '' on off-ramp."""
    logger.error("Convert failed for %s (%s) — trying Grok MD-correction", key, exc)
    # NOTE: the raw local file is already cleaned up; re-download for the fallback.
    local = None
    try:
        fd, local = tempfile.mkstemp(suffix=Path(key).suffix.lower())
        os.close(fd)
        s3.download_file(BUCKET, key, local)
        markdown = _grok_correct_fallback(local, book)
    except Exception as e:  # noqa: BLE001 - fallback must never crash the convert
        logger.warning("Grok MD-correction re-download failed for %s: %s", key, e)
        markdown = None
    finally:
        if local and os.path.exists(local):
            try:
                os.remove(local)
            except OSError:
                pass
    if markdown:
        return markdown
    logger.warning(
        "OBSERV phase0.error reason=convert_failed book=%s key=%s detail=%s "
        "(Grok MD-correction could not recover; off-ramp to review)",
        book,
        key,
        str(exc)[:120],
    )
    from src.ingestion.review_reject import reject_to_review

    reject_to_review(
        s3,
        BUCKET,
        key,
        book,
        f"convert failed ({exc}) and Grok MD-correction could not recover "
        f"usable markdown — needs human review",
        category="convert-failed",
        region=REGION,
    )
    return ""


def _build_meta_yaml(book: str, key: str) -> bytes:
    """Build chapter meta as SAFE YAML (P2-C1). Uses yaml.safe_dump (never an
    f-string) so a book/key with YAML metacharacters cannot inject structure.
    author/series/chapter_title/license are emitted as '' (empty) — NOT a literal
    'TODO - Add ...' that masquerades as real data; the gap is recorded via a
    needs-review/metadata marker instead."""
    meta = {
        "series": "",
        "book": book,
        "author": "",
        "chapter_number": "1",
        "chapter_title": "",
        "license": "",
        "copyright_date": "",
        "source_url": f"converted from {key}",
    }
    return yaml.safe_dump(meta, default_flow_style=False, allow_unicode=True).encode(
        "utf-8"
    )


def _mark_metadata_gap(s3, key: str, book: str) -> None:
    """Record the deferred-metadata gap as a detectable needs-review/metadata
    marker (P2-C1) rather than masquerading empty fields as real TODO strings.
    Fail-safe: the marker is best-effort and never breaks the convert."""
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
    except Exception as e:  # noqa: BLE001 - marker must never crash the convert
        logger.warning("metadata needs-review marker failed for %s: %s", key, e)


def _write_chapter_structure(s3, key: str, book: str, markdown: str) -> str:
    """Write meta FIRST then content (parse fires on the content upload), tag the
    source for cold archive, and record the metadata gap. Returns the content key."""
    meta_key = f"contentrepository/{book}/chapter1/chapter1-meta.yaml"
    content_key = f"contentrepository/{book}/chapter1/chapter1-content.md"
    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=_build_meta_yaml(book, key))
    s3.put_object(Bucket=BUCKET, Key=content_key, Body=markdown.encode("utf-8"))
    _mark_metadata_gap(s3, key, book)
    # Source doc (epub/docx) now consumed — tag it for cold archive. Fail-safe.
    from src.ingestion.archive_tag import tag_source_cold

    tag_source_cold(s3, BUCKET, key)
    logger.info("Converted %s -> %s (%d chars)", key, content_key, len(markdown))
    logger.info(
        "OBSERV phase0.converted book=%s chars=%d key=%s content_key=%s",
        book,
        len(markdown),
        key,
        content_key,
    )
    return content_key


def convert_key(key: str) -> str:
    """Convert one EPUB/DOCX/TXT S3 key to the chapter structure. Returns the
    content output key, or "" on skip/failure (logged, non-fatal).

    FAIL-SAFE (P1-C1): the whole download+detect+convert body is wrapped in a
    broad ``except Exception`` so a malformed / decompression-bomb / ClientError
    upload OFF-RAMPS to needs-review and returns '' instead of crashing the task.
    The existing (ConverterUnavailable, ConverterError) recovery path is kept.
    """
    if not BUCKET:
        raise RuntimeError("S3_BUCKET env var not configured")
    ext = Path(key).suffix.lower()
    media = _EXT_MEDIA.get(ext)
    if not media:
        logger.warning("Unsupported convert extension %s (%s) — skipping", ext, key)
        logger.info(
            "OBSERV phase0.skipped reason=unsupported_ext ext=%s key=%s", ext, key
        )
        return ""

    s3 = boto3.client("s3", region_name=REGION)
    book = _book_from_key(key)
    try:
        markdown = _download_detect_convert(s3, key, book, ext, media)
    except (ConverterUnavailable, ConverterError) as exc:
        markdown = _recover_or_offramp(s3, key, book, exc)
    except Exception as exc:  # noqa: BLE001 - FAIL-SAFE off-ramp (P1-C1)
        # A malformed/zip-bomb/ClientError upload (or any other unexpected error)
        # must NOT crash the task — off-ramp to needs-review and return ''.
        logger.error("Convert crashed for %s (%s) — off-ramping to review", key, exc)
        logger.warning(
            "OBSERV phase0.error reason=convert_error book=%s key=%s detail=%s",
            book,
            key,
            str(exc)[:160],
        )
        try:
            from src.ingestion.review_reject import reject_to_review

            reject_to_review(
                s3,
                BUCKET,
                key,
                book,
                f"convert error (fail-safe off-ramp): {exc}",
                category="convert-error",
                region=REGION,
            )
        except Exception as e:  # noqa: BLE001 - reject must never crash the task
            logger.error("reject_to_review failed for %s: %s", key, e)
        return ""

    if not markdown:
        return ""

    # Normalize to English before promote-to-parse (fail-safe: English/disabled/
    # error -> unchanged). A converted doc is one document -> per-document detect.
    from src.ingestion.translation import normalize_markdown_to_english

    markdown = normalize_markdown_to_english(markdown, per_page=False)
    return _write_chapter_structure(s3, key, book, markdown)


def main() -> int:
    """Entry: convert the CONVERT_KEY document to the chapter structure."""
    key = os.getenv("CONVERT_KEY", "")
    if not key:
        logger.error("CONVERT_KEY env var not set — nothing to convert")
        logger.warning("OBSERV phase0.error reason=no_convert_key")
        return 2
    out = convert_key(key)
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
