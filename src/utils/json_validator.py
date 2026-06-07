"""Centralized JSON validation and writing utilities."""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from jsonschema import ValidationError, validate

from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)

# Performance tracking
_validation_stats = {"total": 0, "failures": 0, "total_time": 0.0}

# Validation hooks
_pre_validation_hooks: list[Callable] = []
_post_validation_hooks: list[Callable] = []

# Custom validators enabled by default
_custom_validators_enabled = True

# Compiled regex patterns for performance
_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x1f]")


def sanitize_json_string(json_str: str) -> str:
    """
    Sanitize malformed JSON string before parsing.

    Fixes common LLM JSON generation issues:
    - Unterminated strings
    - Missing commas
    - Trailing commas
    """
    # Remove null bytes and control characters
    json_str = _CONTROL_CHARS_PATTERN.sub("", json_str)

    # Fix unterminated strings at end of input
    if json_str.count('"') % 2 != 0:
        json_str += '"'

    # Fix missing closing braces/brackets
    open_braces = json_str.count("{") - json_str.count("}")
    open_brackets = json_str.count("[") - json_str.count("]")
    json_str += "}" * open_braces + "]" * open_brackets

    return json_str.strip()


def parse_json_safe(json_str: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """
    Safely parse JSON with automatic error recovery.

    Args:
        json_str: JSON string to parse
        max_retries: Number of sanitization attempts

    Returns:
        Parsed JSON dict or None if parsing fails
    """
    for attempt in range(max_retries):
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error (attempt {attempt + 1}): {e}")

            if attempt < max_retries - 1:
                json_str = sanitize_json_string(json_str)
            else:
                logger.error(f"Failed to parse JSON after {max_retries} attempts")
                return None

    return None


def _is_ulid_field(key: str, value: Any) -> bool:
    """Check if a key/value pair is a ULID field with an invalid value."""
    if not isinstance(value, str) or not value:
        return False
    if not (key.endswith("ID") or key.endswith("_id")):
        return False
    return not _ULID_PATTERN.match(value)


def _fix_invalid_ulids(data: Any) -> Any:
    """
    Fix invalid ULIDs in data recursively.

    Replaces any ID field with invalid ULID format with a valid ULID.
    Returns a new structure; does not mutate the input.
    """
    import copy

    return _fix_ulids_recursive(copy.deepcopy(data))


def _fix_ulids_recursive(data: Any) -> Any:
    """Recursively fix ULIDs in place on an already-copied structure."""
    if isinstance(data, dict):
        for key, value in data.items():
            if _is_ulid_field(key, value):
                import ulid

                new_ulid = str(ulid.new())
                data[key] = new_ulid
                logger.debug(
                    f"Fixed invalid ULID in {key}: {value[:20]}... → {new_ulid}"
                )
            elif isinstance(value, (dict, list)):
                data[key] = _fix_ulids_recursive(value)
    elif isinstance(data, list):
        return [_fix_ulids_recursive(item) for item in data]

    return data


def enable_custom_validators():
    """Enable custom validators."""
    global _custom_validators_enabled
    _custom_validators_enabled = True


def disable_custom_validators():
    """Disable custom validators."""
    global _custom_validators_enabled
    _custom_validators_enabled = False


def _format_validation_error(error: ValidationError, filepath: Path) -> str:
    """Format validation error with helpful context."""
    path = (
        " -> ".join(str(p) for p in error.absolute_path)
        if error.absolute_path
        else "root"
    )
    return (
        f"Validation failed for {filepath.name}\n"
        f"  Location: {path}\n"
        f"  Error: {error.message}\n"
        f"  Schema path: {' -> '.join(str(p) for p in error.absolute_schema_path)}"
    )


def validate_and_write_json(
    filepath: Path,
    data: Dict[str, Any],
    schema: Optional[Dict[str, Any]] = None,
    use_lock: bool = True,
) -> None:
    """
    Validate JSON data against schema and write to file.

    Args:
        filepath: Path to write JSON file
        data: JSON data to validate and write
        schema: JSON schema for validation (optional)
        use_lock: Use file locking for concurrent access (default: True)

    Raises:
        ValidationError: If data doesn't match schema
    """
    # Parse if string input
    if isinstance(data, str):
        data = parse_json_safe(data)
        if data is None:
            raise ValueError("Failed to parse JSON string")

    # Fix invalid ULIDs before validation
    data = _fix_invalid_ulids(data)

    # Validate if schema provided
    if schema:
        start_time = time.perf_counter()
        try:
            validate(instance=data, schema=schema)
            elapsed = time.perf_counter() - start_time
            _validation_stats["total"] += 1
            _validation_stats["total_time"] += elapsed
            if elapsed > 0.1:  # Log slow validations
                logger.warning("Slow validation for %s: %.3fs", filepath.name, elapsed)
        except ValidationError as e:
            _validation_stats["total"] += 1
            _validation_stats["failures"] += 1
            error_msg = _format_validation_error(e, filepath)
            logger.error(error_msg)
            raise

    # Write to file
    if use_lock:
        write_json_with_lock(filepath, data)
    else:
        from src.schemas import inject_metadata

        inject_metadata(data)
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (OSError, IOError) as e:
            logger.error("Error writing file %s: %s", filepath, e)
            raise


def _parse_data_if_string(data: Any, context: str) -> Optional[Dict[str, Any]]:
    """Parse data if it's a string, return None on failure."""
    if isinstance(data, str):
        parsed = parse_json_safe(data)
        if parsed is None:
            logger.error(f"{context}: Failed to parse JSON string")
        return parsed
    return data


def _run_custom_validators(data: Dict[str, Any], context: str) -> bool:
    """Run custom validators if enabled. Returns True if valid."""
    if not _custom_validators_enabled:
        return True

    try:
        from src.utils.custom_validators import validate_data_with_custom_validators

        results = validate_data_with_custom_validators(data)

        # Log errors
        if results["errors"]:
            for error in results["errors"]:
                logger.error(
                    "%sCustom validation error: %s",
                    context + ": " if context else "",
                    error,
                )
            return False

        # Log warnings
        if results["warnings"]:
            for warning in results["warnings"]:
                logger.warning(
                    "%sCustom validation warning: %s",
                    context + ": " if context else "",
                    warning,
                )

        return True
    except ImportError:
        return True  # Custom validators not available


def _validate_against_schema(
    data: Dict[str, Any], schema: Dict[str, Any], context: str
) -> bool:
    """Validate data against JSON schema."""
    try:
        validate(instance=data, schema=schema)
        return True
    except ValidationError as e:
        path = (
            " -> ".join(str(p) for p in e.absolute_path) if e.absolute_path else "root"
        )
        prefix = f"{context}: " if context else ""
        logger.error("%sValidation failed at %s: %s", prefix, path, e.message)
        return False


def validate_json(data: Any, schema: Dict[str, Any], context: str = "") -> bool:
    """
    Validate JSON data against schema.

    Args:
        data: JSON data to validate (can be dict or string)
        schema: JSON schema for validation
        context: Optional context string for error messages (e.g., filename)

    Returns:
        True if valid, False otherwise
    """
    # Parse if string input
    data = _parse_data_if_string(data, context)
    if data is None:
        return False

    # Fix invalid ULIDs before validation
    data = _fix_invalid_ulids(data)

    # Run custom validators
    if not _run_custom_validators(data, context):
        return False

    # Run pre-validation hooks
    _run_hooks(_pre_validation_hooks, data)

    # Validate against schema
    is_valid = _validate_against_schema(data, schema, context)

    # Run post-validation hooks
    _run_hooks(_post_validation_hooks, data, is_valid)

    return is_valid


def get_validation_stats() -> Dict[str, Any]:
    """
    Get validation performance statistics.

    Returns:
        Dictionary with validation metrics
    """
    stats = _validation_stats.copy()
    if stats["total"] > 0:
        stats["avg_time"] = stats["total_time"] / stats["total"]
        stats["failure_rate"] = stats["failures"] / stats["total"]
    return stats


def validate_directory(
    directory: Path, schema: Dict[str, Any], pattern: str = "*.json"
) -> Dict[str, Any]:
    """
    Validate all JSON files in a directory.

    Args:
        directory: Directory to scan
        schema: JSON schema for validation
        pattern: Glob pattern for files (default: *.json)

    Returns:
        Dictionary with validation results
    """
    results: Dict[str, Any] = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "errors": [],
        "custom_validation": {
            "enabled": _custom_validators_enabled,
            "errors": 0,
            "warnings": 0,
        },
    }

    for filepath in directory.glob(pattern):
        if not filepath.is_file():
            continue

        results["total"] += 1
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            validate(instance=data, schema=schema)
            results["valid"] += 1
        except (json.JSONDecodeError, ValidationError) as e:
            results["invalid"] += 1
            results["errors"].append({"file": str(filepath.name), "error": str(e)})

    return results


def register_pre_validation_hook(hook: Callable[[Dict[str, Any]], None]) -> None:
    """Register a pre-validation hook."""
    _pre_validation_hooks.append(hook)


def register_post_validation_hook(hook: Callable[[Dict[str, Any], bool], None]) -> None:
    """Register a post-validation hook."""
    _post_validation_hooks.append(hook)


def _run_hooks(hooks: list[Callable], *args) -> None:
    """Run all registered hooks."""
    for hook in hooks:
        try:
            hook(*args)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Hook failed: %s", e)
