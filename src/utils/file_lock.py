"""File locking utilities for concurrent access."""

import json
import logging
import platform
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Per-file threading locks (prevents in-process races; flock handles cross-process)
_file_locks: Dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _get_file_lock(filepath: Path) -> threading.Lock:
    """Get or create a threading lock for a specific file path."""
    key = str(filepath.resolve())
    with _file_locks_guard:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


@contextmanager
def locked_json(filepath: Path):
    """Context manager that holds an exclusive lock across read-modify-write.

    Usage:
        with locked_json(path) as (data, save):
            data["events"].append(new_event)
            save(data)

    If the file doesn't exist, data is an empty dict.
    Uses threading lock (in-process) + flock (cross-process) for full safety.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _get_file_lock(filepath)
    system = platform.system()

    thread_lock.acquire()
    try:
        if system in ("Linux", "Darwin"):
            import fcntl

            # Open in r+ if exists, else create
            if filepath.exists():
                f = open(filepath, "r+", encoding="utf-8")
            else:
                f = open(filepath, "w+", encoding="utf-8")

            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                content = f.read()
                data = json.loads(content) if content.strip() else {}

                def save(new_data):
                    f.seek(0)
                    f.truncate()
                    json.dump(new_data, f, indent=2, ensure_ascii=False)

                yield data, save
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                f.close()
        else:
            # Fallback: threading lock only (no cross-process safety)
            data = {}
            if filepath.exists():
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)

            def save(new_data):
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(new_data, f, indent=2, ensure_ascii=False)

            yield data, save
    finally:
        thread_lock.release()


# Required fields per entity type (directory name → field list)
_REQUIRED_FIELDS: Dict[str, list] = {
    "people": ["PersonID", "name"],
    "people_groups": ["GroupID", "group_name"],
    "places": ["PlaceID"],
    "dates": ["DateID", "date_start"],
    "equipment": ["EquipmentID", "common_name"],
    "weather": ["WeatherID"],
    "casualties": ["CasualtyID"],
    "logistics": ["LogisticsID"],
}


def _validate_entity(filepath: Path, data: Dict[str, Any]) -> None:
    """Validate required fields before writing. Logs warning on invalid data."""
    # Skip non-entity files (metadata, indexes, reports, tracking)
    if filepath.name in (
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "not_people.json",
        "not_related.json",
    ) or filepath.name.startswith("."):
        return
    entity_type = filepath.parent.name
    required = _REQUIRED_FIELDS.get(entity_type)
    if not required:
        return
    missing = [f for f in required if not data.get(f)]
    if missing:
        logger.warning(
            "Entity validation: %s missing required fields %s",
            filepath.name,
            missing,
        )


def write_json_with_lock(filepath: Path, data: Dict[str, Any]) -> None:
    """Write JSON file with file locking for concurrent access."""
    from src.schemas import inject_metadata

    inject_metadata(data)
    _validate_entity(filepath, data)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Disk space check (local mode only — skip in /tmp/pipeline ECS workdir)
    if not str(filepath).startswith("/tmp/"):
        import shutil

        free = shutil.disk_usage(filepath.parent).free
        if free < 50 * 1024 * 1024:  # 50MB threshold
            raise OSError(
                f"Low disk space ({free // 1024 // 1024}MB free) — aborting write to {filepath.name}"
            )

    # Atomic write: write to temp file, then replace (crash-safe)
    import tempfile

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=filepath.parent, suffix=".tmp", prefix=filepath.stem
    )
    try:
        import os as _os

        with _os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _os.replace(tmp_path, filepath)
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise

    # Dual-write to DynamoDB if enabled (immediate durability)
    _dual_write_dynamo(filepath, data)


# ID field per entity type
_ID_FIELDS: Dict[str, str] = {
    "people": "PersonID",
    "people_groups": "GroupID",
    "places": "PlaceID",
    "dates": "DateID",
    "equipment": "EquipmentID",
    "weather": "WeatherID",
    "casualties": "CasualtyID",
    "logistics": "LogisticsID",
    "bibliography": "BibliographyID",
    "maps": "MapID",
}


def _dual_write_dynamo(filepath: Path, data: Dict[str, Any]) -> None:
    """Write entity to DynamoDB if dual-write is enabled. Non-blocking on failure."""
    entity_type = filepath.parent.name
    id_field = _ID_FIELDS.get(entity_type)
    if not id_field:
        return
    entity_id = data.get(id_field)
    if not entity_id:
        return
    try:
        from src.utils.entity_store import get_entity_store

        store = get_entity_store()
        if store:
            store.put(entity_type, entity_id, data, filename=filepath.name)
    except Exception as e:
        logger.warning(
            "Dual-write to DynamoDB failed for %s/%s: %s", entity_type, entity_id, e
        )
        _track_failed_write(entity_type, entity_id, filepath)


# Track failed dual-writes for reconciliation at Phase 3 start
_failed_writes: List = []
_failed_writes_lock = threading.Lock()


def _track_failed_write(entity_type: str, entity_id: str, filepath: Path) -> None:
    """Record failed DynamoDB write for later reconciliation."""
    with _failed_writes_lock:
        _failed_writes.append(
            {"type": entity_type, "id": entity_id, "path": str(filepath)}
        )


def get_failed_writes() -> List[Dict[str, str]]:
    """Return list of failed dual-writes since last clear."""
    with _failed_writes_lock:
        return list(_failed_writes)


def clear_failed_writes() -> None:
    """Clear tracked failures after successful reconciliation."""
    with _failed_writes_lock:
        _failed_writes.clear()


def read_json_with_lock(filepath: Path) -> Dict[str, Any]:
    """Read JSON file with file locking for concurrent access."""
    if not filepath.exists():
        return {}

    system = platform.system()

    if system in ("Linux", "Darwin"):  # Unix-like
        import fcntl

        with open(filepath, encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    elif system == "Windows":
        import msvcrt  # type: ignore[import]

        with open(filepath, encoding="utf-8") as f:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            try:
                return json.load(f)
            finally:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        # Fallback: no locking
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
