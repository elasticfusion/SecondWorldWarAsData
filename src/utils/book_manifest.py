"""Per-book entity manifest — tracks which entity files belong to which book.

DynamoDB (AWS mode): key = book_manifest#{book}#{entity_type}, value = list of filenames
Local (filesystem mode): output/.manifests/{book}/{entity_type}.json = list of filenames

Used by Phase 3 scoped downloads to avoid fetching the entire entity corpus.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class BookManifest:
    """Track entity files per book."""

    def __init__(self, dynamo_table=None, local_dir: Optional[Path] = None):
        self._table = dynamo_table
        self._local_dir = local_dir

    def register(self, book: str, entity_type: str, filename: str) -> None:
        """Register an entity file as belonging to a book."""
        if self._table:
            self._register_dynamo(book, entity_type, filename)
        elif self._local_dir:
            self._register_local(book, entity_type, filename)

    def get_files(self, book: str, entity_type: str) -> List[str]:
        """Get all entity filenames for a book + entity type."""
        if self._table:
            return self._get_dynamo(book, entity_type)
        if self._local_dir:
            return self._get_local(book, entity_type)
        return []

    def get_all_files(self, book: str) -> dict:
        """Get all entity filenames for a book, keyed by entity type."""
        entity_types = [
            "people",
            "people_groups",
            "places",
            "dates",
            "equipment",
            "weather",
            "logistics",
            "casualties",
            "bibliography",
        ]
        result = {}
        for et in entity_types:
            files = self.get_files(book, et)
            if files:
                result[et] = files
        return result

    # --- DynamoDB ---

    def _make_key(self, book: str, entity_type: str) -> str:
        return f"book_manifest#{book}#{entity_type}"

    def _register_dynamo(self, book: str, entity_type: str, filename: str) -> None:
        key = self._make_key(book, entity_type)
        try:
            self._table.update_item(
                Key={"cache_key": key},
                UpdateExpression="ADD filenames :f",
                ExpressionAttributeValues={":f": {filename}},
            )
        except Exception as e:
            logger.debug("Failed to register %s in manifest: %s", filename, e)

    def _get_dynamo(self, book: str, entity_type: str) -> List[str]:
        key = self._make_key(book, entity_type)
        try:
            resp = self._table.get_item(Key={"cache_key": key})
            item = resp.get("Item")
            if item:
                return list(item.get("filenames", set()))
        except Exception as e:
            logger.debug("Failed to read manifest for %s/%s: %s", book, entity_type, e)
        return []

    # --- Local filesystem ---

    def _local_path(self, book: str, entity_type: str) -> Path:
        assert self._local_dir is not None
        d = self._local_dir / ".manifests" / book
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{entity_type}.json"

    def _register_local(self, book: str, entity_type: str, filename: str) -> None:
        path = self._local_path(book, entity_type)
        existing = set()
        if path.exists():
            try:
                existing = set(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
        existing.add(filename)
        path.write_text(json.dumps(sorted(existing)), encoding="utf-8")

    def _get_local(self, book: str, entity_type: str) -> List[str]:
        path = self._local_path(book, entity_type)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []


def get_book_manifest(output_dir: Optional[Path] = None) -> BookManifest:
    """Factory: returns DynamoDB-backed manifest in AWS mode, local otherwise."""
    from src.utils.config import load_config

    config = load_config()
    aws = config.get("aws", {})

    if aws.get("enabled"):
        import boto3

        region = aws.get("region", "us-east-1")
        table_name = aws.get("cache_table", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        return BookManifest(dynamo_table=table)

    return BookManifest(local_dir=output_dir or Path("output"))
