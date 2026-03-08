"""Batch validation of all event JSON files in directory tree."""

from pathlib import Path
from typing import List, Tuple
from .json_validator import validate_event_json, JSONValidationError


def find_all_event_files(root_dir: Path) -> List[Path]:
    """Find all chapterNN-event.json files recursively."""
    if not isinstance(root_dir, Path):
        raise TypeError(f"root_dir must be a Path, got {type(root_dir).__name__}")
    return sorted(root_dir.rglob("chapter*-event.json"))


def validate_all_event_files(root_dir: Path) -> Tuple[List[Path], List[Tuple[Path, str]]]:
    """
    Validate all event JSON files in directory tree.

    Args:
        root_dir: Root directory to search

    Returns:
        Tuple of (valid_files, failed_files_with_errors)
        where failed_files_with_errors is list of (file_path, error_message)
    """
    if not isinstance(root_dir, Path):
        raise TypeError(f"root_dir must be a Path, got {type(root_dir).__name__}")
    if not root_dir.is_dir():
        raise ValueError(f"root_dir does not exist or is not a directory: {root_dir}")

    event_files = find_all_event_files(root_dir)

    valid = []
    failed = []

    for file_path in event_files:
        try:
            validate_event_json(str(file_path))
            valid.append(file_path)
        except JSONValidationError as e:
            failed.append((file_path, str(e)))
        except (FileNotFoundError, OSError) as e:
            failed.append((file_path, f"File error: {str(e)}"))

    return valid, failed

