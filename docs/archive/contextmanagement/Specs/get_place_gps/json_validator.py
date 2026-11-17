"""JSON schema validation for event files."""

import json
import re
from pathlib import Path
from typing import Any

# Compiled regex for HTML entity detection
_HTML_ENTITY_PATTERN = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#(?:\d+|x[0-9a-fA-F]+));")


class JSONValidationError(Exception):
    """Raised when JSON validation fails."""


def validate_event_json(file_path: str | Path) -> dict[str, Any]:
    """
    Validate event JSON structure recursively.

    Args:
        file_path: Path to event JSON file

    Returns:
        Parsed JSON data if valid

    Raises:
        JSONValidationError: If JSON is malformed or non-standard
    """
    file_path = Path(file_path).resolve()

    from .paths import BOOK_ROOT
    book_root = BOOK_ROOT.resolve()

    if not file_path.is_relative_to(book_root):
        msg = f"Path traversal detected: {file_path} is outside allowed directory"
        raise JSONValidationError(msg)

    if not file_path.is_file():
        raise JSONValidationError(f"File not found or is not a regular file: {file_path}")

    try:
        data = json.loads(file_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise JSONValidationError(f"Invalid JSON in {file_path}: {e}") from e
    except (OSError, PermissionError) as e:
        raise JSONValidationError(f"Cannot read file {file_path}: {e}") from e

    _validate_structure(data, file_path)
    return data


def _validate_structure(data: Any, file_path: Path) -> None:
    """Validate JSON structure recursively."""
    if not isinstance(data, dict):
        raise JSONValidationError(
            f"{file_path}: Root must be a dictionary, got {type(data).__name__}"
        )

    if "Chapter" not in data or "Event" not in data:
        missing = {k for k in ["Chapter", "Event"] if k not in data}
        raise JSONValidationError(
            f"{file_path}: Missing required keys: {missing}"
        )

    # Check for valid sub-event key (Sub-event, Sub-events, or sub_events)
    sub_event_key = None
    for key in ["Sub-event", "Sub-events", "sub_events"]:
        if key in data:
            sub_event_key = key
            break

    if not sub_event_key:
        raise JSONValidationError(
            f"{file_path}: Missing sub-event key. "
            "Expected one of: 'Sub-event', 'Sub-events', 'sub_events'"
        )

    sub_events = data[sub_event_key]
    if not isinstance(sub_events, list):
        raise JSONValidationError(
            f"{file_path}: '{sub_event_key}' must be a list, "
            f"got {type(sub_events).__name__}"
        )

    for idx, sub_event in enumerate(sub_events):
        _validate_sub_event(sub_event, file_path, idx, sub_event_key)


def _validate_sub_event(
    sub_event: Any, file_path: Path, idx: int, parent_key: str
) -> None:
    """Validate individual sub-event structure."""
    if not isinstance(sub_event, dict):
        raise JSONValidationError(
            f"{file_path}: {parent_key}[{idx}] must be a dictionary, "
            f"got {type(sub_event).__name__}"
        )

    # Required fields that must exist
    required_fields = {"Sub-event_summary", "Sub-event_fulltext"}
    missing = required_fields - set(sub_event.keys())
    if missing:
        raise JSONValidationError(
            f"{file_path}: {parent_key}[{idx}] missing required fields: {missing}"
        )

    # Check for non-standard field names
    if "Sub-Event-Maps" in sub_event and "Sub-Events-Maps" not in sub_event:
        raise JSONValidationError(
            f"{file_path}: {parent_key}[{idx}] uses non-standard field name "
            "'Sub-Event-Maps' instead of 'Sub-Events-Maps'"
        )

    # Validate core field types
    if not isinstance(sub_event["Sub-event_summary"], str):
        raise JSONValidationError(
            f"{file_path}: {parent_key}[{idx}].Sub-event_summary must be string"
        )

    if not isinstance(sub_event["Sub-event_fulltext"], dict):
        raise JSONValidationError(
            f"{file_path}: {parent_key}[{idx}].Sub-event_fulltext must be dict"
        )

    # Validate Sub-event_fulltext paragraphs are strings
    for para_key, para_value in sub_event["Sub-event_fulltext"].items():
        if not isinstance(para_value, str):
            raise JSONValidationError(
                f"{file_path}: {parent_key}[{idx}].Sub-event_fulltext.{para_key} "
                f"must be string, got {type(para_value).__name__}"
            )

    # Validate Sub-Events-Maps format if present
    if "Sub-Events-Maps" in sub_event:
        maps = sub_event["Sub-Events-Maps"]
        if not isinstance(maps, list):
            raise JSONValidationError(
                f"{file_path}: {parent_key}[{idx}].Sub-Events-Maps must be a list, "
                f"got {type(maps).__name__}"
            )
        for map_idx, map_item in enumerate(maps):
            if not isinstance(map_item, list):
                raise JSONValidationError(
                    f"{file_path}: {parent_key}[{idx}].Sub-Events-Maps[{map_idx}] "
                    "must be [url, description] pair"
                )
            if len(map_item) != 2:
                raise JSONValidationError(
                    f"{file_path}: {parent_key}[{idx}].Sub-Events-Maps[{map_idx}] "
                    "must have exactly 2 elements"
                )
            if not (isinstance(map_item[0], str) and isinstance(map_item[1], str)):
                raise JSONValidationError(
                    f"{file_path}: {parent_key}[{idx}].Sub-Events-Maps[{map_idx}] "
                    "must contain two strings"
                )

    # Check for HTML entities
    _check_html_entities(sub_event, file_path, idx, parent_key)


def _check_html_entities(
    sub_event: dict[str, Any], file_path: Path, idx: int, parent_key: str
) -> None:
    """Check for HTML entities which indicate non-standard encoding."""
    if not isinstance(sub_event, dict):
        raise JSONValidationError(
            f"{file_path}: {parent_key}[{idx}] must be a dict for HTML entity check"
        )

    def check_value(value: Any, path: str) -> None:
        if isinstance(value, str):
            if _HTML_ENTITY_PATTERN.search(value):
                raise JSONValidationError(
                    f"{file_path}: {path} contains HTML entities "
                    "(use proper Unicode characters instead)"
                )
        elif isinstance(value, dict):
            for k, v in value.items():
                check_value(v, f"{path}.{k}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                check_value(item, f"{path}[{i}]")

    check_value(sub_event, f"{parent_key}[{idx}]")
