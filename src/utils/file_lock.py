"""File locking utilities for concurrent access."""

import json
import logging
import platform
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


@contextmanager
def locked_json(filepath: Path):
    """Context manager that holds an exclusive lock across read-modify-write.

    Usage:
        with locked_json(path) as (data, save):
            data["events"].append(new_event)
            save(data)

    If the file doesn't exist, data is an empty dict.
    The lock is held from entry until exit, preventing races.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    system = platform.system()

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
        # Fallback: no locking
        data = {}
        if filepath.exists():
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)

        def save(new_data):
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(new_data, f, indent=2, ensure_ascii=False)

        yield data, save


def write_json_with_lock(filepath: Path, data: Dict[str, Any]) -> None:
    """Write JSON file with file locking for concurrent access."""
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Platform-specific locking
    system = platform.system()

    if system in ("Linux", "Darwin"):  # Unix-like
        import fcntl

        with open(filepath, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    elif system == "Windows":
        import msvcrt  # type: ignore[import]

        with open(filepath, "w", encoding="utf-8") as f:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            try:
                json.dump(data, f, indent=2)
            finally:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        # Fallback: no locking
        logger.warning("File locking not supported on %s, writing without lock", system)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


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
