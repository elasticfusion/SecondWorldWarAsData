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
    from src.utils.backends import create_cache_backend, create_storage
    from src.utils.config import load_config

    config = load_config()
    storage = create_storage(config, Path("."))

    records = _extract_s3_records(event)
    results = {"processed": 0, "failed": 0}

    for bucket, key in records:
        try:
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
    prefix = f"content/{book_name}"
    for path in storage.list_files(prefix, "*"):
        data = storage.read_bytes(path)
        local = tmpdir / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)


def _run_parse(tmpdir: Path, book_name: str, storage) -> None:
    """Run Phase 1 parsing and upload results to storage."""
    from src.parser import parse_book

    content_dir = tmpdir / "content" / book_name
    output_dir = tmpdir / "output" / book_name
    output_dir.mkdir(parents=True, exist_ok=True)

    parse_book(content_dir, output_dir)

    # Upload parsed files to storage
    for f in output_dir.glob("*.json"):
        dest = f"output/{book_name}/{f.name}"
        storage.write_json(dest, json.loads(f.read_text(encoding="utf-8")))
        logger.info("Uploaded: %s", dest)
