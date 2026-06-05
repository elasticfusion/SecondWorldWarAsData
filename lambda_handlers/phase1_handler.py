"""Lambda handler for Phase 1: Parse markdown to JSON.

Triggered by S3 event (via SNS) when markdown files are uploaded to content/ prefix.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


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
            book_name = key.split("/")[1]  # content/{BookName}/...
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
    """Download all content files for a book to local temp dir."""
    prefix = f"contentrepository/{book_name}"
    for path in storage.list_files(prefix, "*"):
        data = storage.read_bytes(path)
        local = tmpdir / path
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
        logger.warning("No chapters found for %s", book_name)
        return

    for chapter_group in chapters:
        documents = parse_chapter(chapter_group)
        for doc in documents:
            section_suffix = doc.section_id if doc.section_id else "full"
            filename = f"chapter{doc.chapter_number}{section_suffix}-parsed.json"
            output_data = {
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
                    {"number": f.number, "url": f.url} for f in doc.footnotes
                ],
            }
            dest = f"output/content/{book_name}/{filename}"
            storage.write_json(dest, output_data)
            logger.info("Uploaded: %s", dest)
