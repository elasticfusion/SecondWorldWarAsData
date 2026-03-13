"""Schema evolution and migration utilities."""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Migration registry: {schema_name: {from_version: {to_version: migration_func}}}
_migrations: Dict[str, Dict[str, Dict[str, Callable]]] = {}


def register_migration(
    schema_name: str, from_version: str, to_version: str
) -> Callable:
    """
    Register a migration function for schema evolution.

    Args:
        schema_name: Name of schema (e.g., 'people')
        from_version: Source version
        to_version: Target version

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        if schema_name not in _migrations:
            _migrations[schema_name] = {}
        if from_version not in _migrations[schema_name]:
            _migrations[schema_name][from_version] = {}
        _migrations[schema_name][from_version][to_version] = func
        return func

    return decorator


def get_data_version(data: Dict[str, Any]) -> Optional[str]:
    """Extract version from data."""
    return data.get("version") or data.get("schema_version")


def detect_schema_version(filepath: Path) -> Optional[str]:
    """
    Detect schema version from a JSON file.

    Args:
        filepath: Path to JSON file

    Returns:
        Version string or None if not found
    """
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return get_data_version(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Error reading %s: %s", filepath, e)
        return None


def migrate_data(
    data: Dict[str, Any],
    schema_name: str,
    from_version: str,
    to_version: str,
) -> Dict[str, Any]:
    """
    Migrate data from one version to another.

    Args:
        data: Data to migrate
        schema_name: Schema name
        from_version: Current version
        to_version: Target version

    Returns:
        Migrated data

    Raises:
        ValueError: If migration path not found
    """
    if from_version == to_version:
        return data

    # Find migration path
    if schema_name not in _migrations:
        raise ValueError(f"No migrations registered for schema: {schema_name}")

    if from_version not in _migrations[schema_name]:
        raise ValueError(
            f"No migration from version {from_version} for schema {schema_name}"
        )

    if to_version not in _migrations[schema_name][from_version]:
        raise ValueError(
            f"No migration from {from_version} to {to_version} for schema {schema_name}"
        )

    # Apply migration
    migration_func = _migrations[schema_name][from_version][to_version]
    migrated = migration_func(data)

    # Update version in data
    if "version" in migrated:
        migrated["version"] = to_version
    elif "schema_version" in migrated:
        migrated["schema_version"] = to_version

    return migrated


def migrate_file(
    filepath: Path,
    schema_name: str,
    to_version: str,
    backup: bool = True,
) -> bool:
    """
    Migrate a single file to target version.

    Args:
        filepath: Path to file
        schema_name: Schema name
        to_version: Target version
        backup: Create backup before migration

    Returns:
        True if migrated, False if already at target version
    """
    data = json.loads(filepath.read_text(encoding="utf-8"))
    from_version = get_data_version(data)

    if from_version == to_version:
        logger.info("%s already at version %s", filepath.name, to_version)
        return False

    if backup:
        backup_path = filepath.with_suffix(f".{from_version}.bak")
        backup_path.write_text(filepath.read_text(encoding="utf-8"))
        logger.info("Created backup: %s", backup_path.name)

    migrated = migrate_data(data, schema_name, from_version or "1.0", to_version)

    filepath.write_text(json.dumps(migrated, indent=2, ensure_ascii=False))
    logger.info("Migrated %s from %s to %s", filepath.name, from_version, to_version)
    return True


def scan_versions(directory: Path, pattern: str = "*.json") -> Dict[str, int]:
    """
    Scan directory and count files by version.

    Args:
        directory: Directory to scan
        pattern: File pattern

    Returns:
        Dictionary mapping version to count
    """
    versions: Dict[str, int] = {}

    for filepath in directory.glob(pattern):
        if not filepath.is_file():
            continue

        version = detect_schema_version(filepath)
        version_key = version or "unknown"
        versions[version_key] = versions.get(version_key, 0) + 1

    return versions


def generate_migration_report(directory: Path, schema_name: str) -> str:
    """
    Generate migration report for a directory.

    Args:
        directory: Directory to analyze
        schema_name: Schema name

    Returns:
        Formatted report string
    """
    versions = scan_versions(directory)
    total = sum(versions.values())

    lines = [
        f"Migration Report: {directory}",
        f"Schema: {schema_name}",
        f"Total files: {total}",
        "",
        "Version distribution:",
    ]

    for version, count in sorted(versions.items()):
        pct = (count / total * 100) if total > 0 else 0
        lines.append(f"  {version}: {count} files ({pct:.1f}%)")

    return "\n".join(lines)
