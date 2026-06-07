"""Incremental dedup support — track last run time, identify new files."""

import logging
import os
import time
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)


def get_last_dedup_run(entity_type: str) -> Optional[float]:
    """Get the timestamp of the last dedup run for this entity type from DynamoDB."""
    try:
        import boto3

        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        region = os.environ.get("AWS_REGION", "us-east-1")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        resp = table.get_item(Key={"cache_key": f"dedup_run#{entity_type}"})
        item = resp.get("Item")
        if item:
            return float(item.get("timestamp", 0))
    except Exception:
        pass
    return None


def set_last_dedup_run(entity_type: str) -> None:
    """Record the current timestamp as the last dedup run for this entity type."""
    try:
        import boto3

        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        region = os.environ.get("AWS_REGION", "us-east-1")
        table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        table.put_item(
            Item={
                "cache_key": f"dedup_run#{entity_type}",
                "timestamp": int(time.time()),
            }
        )
    except Exception as e:
        logger.debug("Failed to set last dedup run: %s", e)


def get_new_files(entity_dir: Path, since: Optional[float]) -> Set[str]:
    """Return filenames modified after `since` timestamp. If since is None, all are 'new'."""
    if since is None:
        return set()  # No previous run — treat as full mode (empty = don't filter)
    new = set()
    for f in entity_dir.glob("*.json"):
        if f.name in ("index.json", "duplicate_report.json", "not_duplicates.json"):
            continue
        if f.stat().st_mtime > since:
            new.add(f.name)
    return new


def should_compare(file1: str, file2: str, new_files: Set[str]) -> bool:
    """Return True if this pair should be compared (at least one file is new)."""
    if not new_files:
        return True  # Full mode — compare all
    return file1 in new_files or file2 in new_files
