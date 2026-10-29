"""Lambda handler for Phase 1: Parse markdown to JSON.

Triggered by S3 event (via SNS) when markdown files are uploaded to content/ prefix.
"""

import json
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# A book_name is a single S3 path segment used to build a prefix. Restrict it to
# a safe charset so a hostile/garbage key can't inject path separators or
# traversal into the storage prefix we list.
_BOOK_NAME_RE = re.compile(r"[A-Za-z0-9 _-]+")


def _safe_book_name(key: str) -> str:
    """Extract and validate the book name from an S3 key (``content/{Book}/...``).

    Guards against (1) keys with fewer than 2 segments (IndexError) and
    (2) book names outside the safe charset. On either failure emits a LOUD
    OBSERV phase1.book_rejected and returns a safe sentinel fallback.
    """
    segments = key.split("/")
    if len(segments) < 2 or not segments[1]:
        logger.warning(
            "OBSERV phase1.book_rejected key=%s reason=too_few_segments", key
        )
        return "_unknown_book"
    book_name = segments[1]
    if not _BOOK_NAME_RE.fullmatch(book_name):
        logger.warning(
            "OBSERV phase1.book_rejected key=%s book=%s reason=invalid_charset",
            key,
            book_name,
        )
        return "_unknown_book"
    return book_name


def handler(event, _context):
    """Process SNS → S3 event: parse uploaded markdown content."""
    from src.utils.config import load_config
    from src.utils.storage import S3Storage

    config = load_config()
    region = config.get("aws", {}).get("region", "us-east-1")

    records = _extract_s3_records(event)
    results = {"processed": 0, "failed": 0}

    for bucket, key in records:
        try:
            storage = S3Storage(bucket=bucket, region=region)
            book_name = _safe_book_name(key)  # content/{BookName}/...
            logger.info("Parsing: %s/%s", bucket, key)

            # Download to temp dir for local parsing
            with tempfile.TemporaryDirectory() as tmpdir:
                _download_book(storage, book_name, Path(tmpdir))
                _run_parse(Path(tmpdir), book_name, storage)

            results["processed"] += 1
        except Exception as e:
            logger.error("Failed to parse %s: %s", key, e)
            results["failed"] += 1

    return results


def _extract_s3_records(event: dict) -> list[tuple[str, str]]:
    """Extract (bucket, key) pairs from SNS → S3 event."""
    records = []
    for record in event.get("Records", []):
        # SNS wraps S3 events in Message
        message = record.get("Sns", {}).get("Message", "{}")
        s3_event = json.loads(message) if isinstance(message, str) else message
        for s3_record in s3_event.get("Records", []):
            bucket = s3_record["s3"]["bucket"]["name"]
            key = s3_record["s3"]["object"]["key"]
            records.append((bucket, key))
    return records


def _download_book(storage, book_name: str, tmpdir: Path) -> None:
    """Download all content files for a book to local temp dir.

    ``path`` is a FULL S3 key from storage.list_files and may contain ``..``.
    Each resolved destination MUST stay under ``tmpdir`` or it is skipped with a
    loud OBSERV phase1.path_traversal_blocked (never write outside tmpdir).
    """
    tmp_root = tmpdir.resolve()
    prefix = f"contentrepository/{book_name}"
    for path in storage.list_files(prefix, "*"):
        local = tmpdir / path
        resolved = local.resolve()
        # Containment check BEFORE any read/write. is_relative_to (3.9+).
        if resolved != tmp_root and not resolved.is_relative_to(tmp_root):
            logger.warning(
                "OBSERV phase1.path_traversal_blocked book=%s key=%s resolved=%s",
                book_name,
                path,
                resolved,
            )
            continue
        data = storage.read_bytes(path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)


def _run_parse(tmpdir: Path, book_name: str, storage) -> None:
    """Run Phase 1 parsing and upload results to storage."""
    from src.discovery import discover_content_structure
    from src.parser import parse_chapter

    output_dir = tmpdir / "output" / book_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Discover chapter structure within the downloaded book
    structure = discover_content_structure(tmpdir / "content")
    chapters = structure.get(book_name, [])
    if not chapters:
        logger.warning("OBSERV phase1.skipped book=%s reason=no_chapters", book_name)
        logger.warning("No chapters found for %s", book_name)
        return

    parsed_files = 0
    for chapter_group in chapters:
        for doc in parse_chapter(chapter_group):
            if _write_parsed_doc(doc, book_name, storage):
                parsed_files += 1

    if parsed_files:
        logger.info(
            "OBSERV phase1.parsed book=%s chapters=%d parsed_files=%d",
            book_name,
            len(chapters),
            parsed_files,
        )
    else:
        logger.warning(
            "OBSERV phase1.skipped book=%s reason=no_parsed_output chapters=%d",
            book_name,
            len(chapters),
        )


def _doc_to_output(doc) -> dict:
    """Build the serializable parsed-output dict for one document."""
    return {
        "book": doc.book,
        "chapter_number": doc.chapter_number,
        "chapter_title": doc.chapter_title,
        "section_id": doc.section_id,
        "author": doc.author,
        "series": doc.series,
        "license": doc.license,
        "source_file": str(doc.file_path),
        "paragraphs": [
            {
                "absolute_number": p.absolute_number,
                "text": p.text,
                "page_number": p.page_number,
                "section_id": p.section_id,
                "source_file": p.source_file,
            }
            for p in doc.paragraphs
        ],
        "images": [
            {
                "type": img.type,
                "resource_id": img.resource_id,
                "url": img.url,
                "alt_text": img.alt_text,
                "caption": img.caption,
            }
            for img in doc.images
        ],
        "maps": [
            {"url": m.url, "description": m.description, "map_id": m.map_id}
            for m in doc.maps
        ],
        "footnotes": [
            {"number": f.number, "url": f.url, "text": f.text} for f in doc.footnotes
        ],
    }


def _write_parsed_doc(doc, book_name: str, storage) -> bool:
    """Guard + upload one parsed document. Returns True if written."""
    from src.utils.file_lock import guard_parsed_content

    section_suffix = doc.section_id if doc.section_id else "full"
    filename = f"chapter{doc.chapter_number}{section_suffix}-parsed.json"
    output_data = _doc_to_output(doc)
    dest = f"output/content/{book_name}/{filename}"

    if guard_parsed_content(filename, output_data):
        storage.write_json(dest, output_data)
        logger.info("Uploaded: %s", dest)
        return True
    logger.error(
        "DROPPED schema-invalid parse %s — re-parse needed (not uploaded)",
        dest,
    )
    return False
