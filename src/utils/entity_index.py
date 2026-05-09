"""Build entity name→ID indexes from filenames without reading file contents.

Local mode: glob directory for *.json, parse name and ID from filename or index.json.
AWS mode: S3 list_objects, parse name and ID from S3 keys.

All extraction modules that need cross-referencing (casualties, supplemental,
equipment, logistics) should use these functions instead of scanning and parsing
every JSON file.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Files to skip when building indexes
_SKIP_FILES = frozenset(
    [
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "not_related.json",
        ".processed_events.json",
        "related_groups_report.json",
        "review_queue.json",
    ]
)

# ULID pattern: 8+ chars from the ULID alphabet at end of stem
_ULID_SUFFIX = re.compile(r"_([0-9A-HJKMNP-TV-Z]{8,26})$")


def _parse_filename(stem: str) -> tuple[str, Optional[str]]:
    """Extract (name, id_or_none) from a filename stem.

    Handles two patterns:
      'dwight d. eisenhower'          → ('dwight d. eisenhower', None)
      '105mm_Field_Gun_01KPST7X'      → ('105mm Field Gun', '01KPST7X')
    """
    match = _ULID_SUFFIX.search(stem)
    if match:
        name = stem[: match.start()].replace("_", " ").strip()
        return name, match.group(1)
    return stem.replace("_", " ").strip(), None


def build_name_index(
    entity_dir: Path,
    id_field: str,
    _name_field: str,
) -> Dict[str, str]:
    """Build name→ID index from local files without reading contents.

    Tries index.json first (single file read). Falls back to reading
    individual files only when IDs aren't in filenames.

    Args:
        entity_dir: Path to entity directory (e.g., output/people/)
        id_field: ID field name (e.g., 'PersonID')
        _name_field: Name field name (unused — kept for API compat)

    Returns:
        Dict mapping lowercase name → entity ID
    """
    if not entity_dir.exists():
        return {}

    # Try index.json first — maps name → filename
    index_file = entity_dir / "index.json"
    if index_file.exists():
        return _build_from_index_json(entity_dir, index_file, id_field)

    # Fall back to filename parsing
    return _build_from_filenames(entity_dir, id_field)


def _build_from_index_json(
    entity_dir: Path, index_file: Path, id_field: str
) -> Dict[str, str]:
    """Build name→ID from index.json + filename parsing."""
    try:
        raw = json.loads(index_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _build_from_filenames(entity_dir, id_field)

    index: Dict[str, str] = {}
    for name, filename in raw.items():
        stem = Path(filename).stem
        _, file_id = _parse_filename(stem)
        if file_id:
            index[name.lower()] = file_id
        else:
            # ID not in filename — must read the file
            entity_id = _read_id_from_file(entity_dir / filename, id_field)
            if entity_id:
                index[name.lower()] = entity_id

    logger.debug(
        "Built %s index from index.json: %d entries", entity_dir.name, len(index)
    )
    return index


def _build_from_filenames(entity_dir: Path, id_field: str) -> Dict[str, str]:
    """Build name→ID by parsing filenames, reading files only when needed."""
    index: Dict[str, str] = {}
    needs_read = []

    for f in entity_dir.glob("*.json"):
        if f.name in _SKIP_FILES:
            continue
        name, file_id = _parse_filename(f.stem)
        if file_id:
            index[name.lower()] = file_id
        else:
            needs_read.append((name, f))

    # Read files only for entries without IDs in filename
    for name, f in needs_read:
        entity_id = _read_id_from_file(f, id_field)
        if entity_id:
            index[name.lower()] = entity_id

    logger.debug(
        "Built %s index from filenames: %d entries (%d needed file read)",
        entity_dir.name,
        len(index),
        len(needs_read),
    )
    return index


def _read_id_from_file(filepath: Path, id_field: str) -> Optional[str]:
    """Read just the ID field from a JSON file."""
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return data.get(id_field) or data.get("GroupID")
    except (json.JSONDecodeError, OSError):
        return None


def build_name_index_s3(
    s3_client,
    bucket: str,
    prefix: str,
    id_field: str,
) -> Dict[str, str]:
    """Build name→ID index from S3 keys without downloading files.

    For entity types with IDs in filenames (equipment), this is pure listing.
    For others (people, places, groups), downloads only index.json.

    Args:
        s3_client: boto3 S3 client
        bucket: S3 bucket name
        prefix: S3 prefix (e.g., 'output/people/')
        id_field: ID field name (e.g., 'PersonID')

    Returns:
        Dict mapping lowercase name → entity ID
    """
    # Try index.json first (single S3 GET)
    index_key = f"{prefix}index.json"
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=index_key)
        raw = json.loads(resp["Body"].read())
        return _build_from_s3_index_json(raw, s3_client, bucket, prefix, id_field)
    except s3_client.exceptions.NoSuchKey:
        pass
    except Exception:
        pass

    # Fall back to listing keys
    return _build_from_s3_listing(s3_client, bucket, prefix, id_field)


def _build_from_s3_index_json(
    raw: dict, s3_client, bucket: str, prefix: str, id_field: str
) -> Dict[str, str]:
    """Build name→ID from S3 index.json + filename parsing."""
    index: Dict[str, str] = {}
    needs_read = []

    for name, filename in raw.items():
        stem = Path(filename).stem
        _, file_id = _parse_filename(stem)
        if file_id:
            index[name.lower()] = file_id
        else:
            needs_read.append((name, f"{prefix}{filename}"))

    # Batch-read files that need ID extraction
    for name, key in needs_read:
        try:
            resp = s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(resp["Body"].read())
            entity_id = data.get(id_field) or data.get("GroupID")
            if entity_id:
                index[name.lower()] = entity_id
        except Exception:
            pass

    logger.debug(
        "Built S3 index from index.json: %d entries (%d needed file read)",
        len(index),
        len(needs_read),
    )
    return index


def _build_from_s3_listing(
    s3_client, bucket: str, prefix: str, id_field: str
) -> Dict[str, str]:
    """Build name→ID from S3 key listing."""
    index: Dict[str, str] = {}
    needs_read = []

    for page in s3_client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.split("/")[-1]
            if filename in _SKIP_FILES or not filename.endswith(".json"):
                continue
            stem = Path(filename).stem
            name, file_id = _parse_filename(stem)
            if file_id:
                index[name.lower()] = file_id
            else:
                needs_read.append((name, key))

    for name, key in needs_read:
        try:
            resp = s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(resp["Body"].read())
            entity_id = data.get(id_field) or data.get("GroupID")
            if entity_id:
                index[name.lower()] = entity_id
        except Exception:
            pass

    logger.debug(
        "Built S3 index from listing: %d entries (%d needed file read)",
        len(index),
        len(needs_read),
    )
    return index
