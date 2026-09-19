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
    name_field: str = "name",
) -> Dict[str, str]:
    """Build name→ID index from local files.

    Prefers ``index.json`` (fast, single read) but validates it: if a large
    share of its entries point to files that no longer exist (a stale index),
    it is abandoned in favor of a content scan that reads each entity file's
    authoritative ``name``/ID fields. This prevents the silent match-rate
    collapse seen when an index.json drifts from the files on disk.

    Args:
        entity_dir: Path to entity directory (e.g., output/people/)
        id_field: ID field name (e.g., 'PersonID')
        name_field: Name field to read from file contents (default 'name').

    Returns:
        Dict mapping lowercase name → entity ID
    """
    if not entity_dir.exists():
        return {}

    index_file = entity_dir / "index.json"
    if index_file.exists() and not _index_json_is_stale(entity_dir, index_file):
        return _build_from_index_json(entity_dir, index_file, id_field)

    # No index, or a stale one: read authoritative name/ID from file contents.
    return _build_from_contents(entity_dir, id_field, name_field)


# If more than this fraction of index.json entries point to missing files, the
# index is treated as stale and rebuilt from file contents.
_STALE_INDEX_THRESHOLD = 0.2


def _index_json_is_stale(entity_dir: Path, index_file: Path) -> bool:
    """True if too many index.json entries point to files that don't exist."""
    try:
        raw = json.loads(index_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    if not raw:
        return True
    missing = sum(
        1 for filename in raw.values() if not (entity_dir / filename).exists()
    )
    return missing / len(raw) > _STALE_INDEX_THRESHOLD


def _build_from_contents(
    entity_dir: Path, id_field: str, name_field: str
) -> Dict[str, str]:
    """Build name→ID by reading each entity file's name/ID fields.

    Authoritative but slower: reads every file. Used when no index.json exists
    or the existing one is stale. The in-file ``name`` field is preferred over
    the filename stem (stems drop punctuation/casing and can be ambiguous).
    """
    index: Dict[str, str] = {}
    for f in sorted(entity_dir.glob("*.json")):
        if f.name in _SKIP_FILES:
            continue
        name, entity_id = _name_and_id_from_file(f, id_field, name_field)
        if entity_id and name:
            index.setdefault(name.lower(), entity_id)
    logger.debug(
        "Built %s index from file contents: %d entries", entity_dir.name, len(index)
    )
    return index


def _name_and_id_from_file(
    filepath: Path, id_field: str, name_field: str
) -> tuple[Optional[str], Optional[str]]:
    """Return (name, id) for an entity file, falling back to the filename stem."""
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    entity_id = data.get(id_field) or data.get("GroupID")
    name = data.get(name_field)
    if not entity_id or not name:
        stem_name, stem_id = _parse_filename(filepath.stem)
        name = name or stem_name
        entity_id = entity_id or stem_id
    return name, entity_id


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
