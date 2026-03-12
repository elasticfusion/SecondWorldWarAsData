"""Custom validators for data validation."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# Compiled regex patterns for performance
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x1f]")
_JSON_BLOCK_PATTERN = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
_CODE_BLOCK_PATTERN = re.compile(r"```\s*\n(.*?)\n```", re.DOTALL)
_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class ValidationError(Exception):
    """Custom validation error."""


def sanitize_json_response(response: str) -> str:
    """
    Sanitize JSON response from LLM before parsing.

    Args:
        response: Raw JSON string from LLM

    Returns:
        Sanitized JSON string
    """
    # Remove null bytes and control characters
    response = _CONTROL_CHARS_PATTERN.sub("", response)

    # Extract JSON from markdown code blocks if present
    if "```json" in response:
        match = _JSON_BLOCK_PATTERN.search(response)
        if match:
            response = match.group(1)
    elif "```" in response:
        match = _CODE_BLOCK_PATTERN.search(response)
        if match:
            response = match.group(1)

    return response.strip()


def validate_ulid(value: str) -> bool:
    """
    Validate ULID format.

    Args:
        value: String to validate

    Returns:
        True if valid ULID

    Raises:
        ValidationError: If invalid
    """
    if not _ULID_PATTERN.match(value):
        raise ValidationError(
            f"Invalid ULID format: {value}. Must be 26 characters [0-9A-HJKMNP-TV-Z]"
        )
    return True


def validate_iso_date(value: str) -> bool:
    """
    Validate ISO 8601 date format.

    Args:
        value: Date string to validate

    Returns:
        True if valid date

    Raises:
        ValidationError: If invalid
    """
    formats = ["%Y-%m-%d", "%Y-%m", "%Y"]

    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue

    raise ValidationError(
        f"Invalid date format: {value}. Expected ISO 8601 (YYYY-MM-DD, YYYY-MM, or YYYY)"
    )


def validate_url(value: str) -> bool:
    """
    Validate URL format.

    Args:
        value: URL string to validate

    Returns:
        True if valid URL

    Raises:
        ValidationError: If invalid
    """
    try:
        result = urlparse(value)
        if not all([result.scheme, result.netloc]):
            raise ValidationError(f"Invalid URL: {value}. Missing scheme or netloc")
        if result.scheme not in ["http", "https"]:
            raise ValidationError(
                f"Invalid URL scheme: {result.scheme}. Must be http or https"
            )
        return True
    except Exception as e:
        raise ValidationError(f"Invalid URL: {value}. {e}") from e


def validate_cross_reference(
    id_value: str, id_type: str, data_dir: Path
) -> Optional[bool]:
    """
    Validate that a referenced ID exists.

    Args:
        id_value: ID to validate
        id_type: Type of ID (PersonID, EventID, etc.)
        data_dir: Base directory for data files

    Returns:
        True if reference exists, None if directory doesn't exist

    Raises:
        ValidationError: If reference not found
    """
    # Map ID types to directories
    type_map = {
        "PersonID": "people",
        "EventID": "events",
        "EquipmentID": "equipment",
        "MapID": "maps",
        "GroupID": "people_groups",
    }

    dir_name = type_map.get(id_type)
    if not dir_name:
        return None  # Unknown type, skip validation

    target_dir = data_dir / dir_name
    if not target_dir.exists():
        return None  # Directory doesn't exist yet, skip validation

    # Search for file containing this ID
    import json  # Import here to avoid circular dependency

    for json_file in target_dir.glob("*.json"):
        try:
            import json

            data = json.loads(json_file.read_text())
            if data.get(id_type) == id_value:
                return True
        except (json.JSONDecodeError, OSError):
            continue

    raise ValidationError(
        f"Cross-reference validation failed: {id_type}={id_value} not found in {target_dir}"
    )


def _validate_ulid_fields(data: Dict[str, Any]) -> list:
    """Validate all ULID fields in data."""
    errors = []
    for key, value in data.items():
        if "ID" in key and isinstance(value, str):
            try:
                validate_ulid(value)
            except ValidationError as e:
                errors.append(f"{key}: {e}")
    return errors


def _validate_date_fields(data: Dict[str, Any]) -> list:
    """Validate all date fields in data."""
    errors = []
    date_fields = ["birth_date", "death_date", "date_start", "date_end", "date_awarded"]
    for field in date_fields:
        if field in data and data[field] and data[field] != "null":
            try:
                validate_iso_date(data[field])
            except ValidationError as e:
                errors.append(f"{field}: {e}")
    return errors


def _validate_url_fields(data: Dict[str, Any]) -> list:
    """Validate all URL fields in data."""
    errors = []
    url_fields = ["url", "source", "image_path"]
    for field in url_fields:
        if field in data and data[field]:
            value = data[field]
            if isinstance(value, str) and (
                value.startswith("http://") or value.startswith("https://")
            ):
                try:
                    validate_url(value)
                except ValidationError as e:
                    errors.append(f"{field}: {e}")
    return errors


def _validate_cross_references(data: Dict[str, Any], data_dir: Path) -> list:
    """Validate cross-references (returns warnings)."""
    warnings = []
    ref_fields = {
        "PersonID": "PersonID",
        "EventID": "EventID",
        "EquipmentID": "EquipmentID",
    }
    for field, id_type in ref_fields.items():
        if field in data and data[field]:
            try:
                result = validate_cross_reference(data[field], id_type, data_dir)
                if result is False:
                    warnings.append(f"{field}: Reference not found (may not exist yet)")
            except ValidationError as e:
                warnings.append(str(e))
    return warnings


def validate_data_with_custom_validators(
    data: Dict[str, Any], data_dir: Path = Path("output")
) -> Dict[str, list]:
    """
    Run custom validators on data.

    Args:
        data: Data to validate
        data_dir: Base directory for cross-reference validation

    Returns:
        Dictionary with validation results
    """
    errors = []
    errors.extend(_validate_ulid_fields(data))
    errors.extend(_validate_date_fields(data))
    errors.extend(_validate_url_fields(data))
    warnings = _validate_cross_references(data, data_dir)

    return {"errors": errors, "warnings": warnings}
