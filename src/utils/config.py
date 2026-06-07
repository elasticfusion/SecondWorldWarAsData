"""Configuration file loading and path management."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def get_aws_region() -> str:
    """Get AWS region from env var, config, or default. Single source of truth."""
    return os.environ.get(
        "AWS_REGION",
        os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def should_reprocess(entity_type: str) -> bool:
    """Check if an entity type should be re-extracted (ignoring processed registry)."""
    try:
        config = load_config()
        types = config.get("processing", {}).get("reprocess_types", [])
        return entity_type in types
    except Exception:
        return False


# Entity directories that live directly under output_root (not book content)
ENTITY_DIRS = frozenset(
    [
        "dates",
        "places",
        "people",
        "people_groups",
        "equipment",
        "casualties",
        "weather",
        "logistics",
        "maps",
        "maps_images",
        "external_maps",
        "bibliography",
        "supplemental",
        "images",
        "content",
        "metrics",
        "dedup",
    ]
)


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from YAML file with validation."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    _validate_config(config, config_path)
    return config


def _validate_config(config: Dict[str, Any], path: Path) -> None:
    """Validate critical config sections. Raises ValueError on invalid config."""
    if not isinstance(config, dict):
        raise ValueError(f"Config at {path} is not a valid YAML mapping")

    # Required top-level sections
    required = ["paths", "api"]
    for section in required:
        if section not in config:
            raise ValueError(f"Config missing required section: '{section}'")

    # API config
    api = config.get("api", {})
    grok = api.get("grok", {})
    if grok.get("model") and not isinstance(grok["model"], str):
        raise ValueError("api.grok.model must be a string")

    rpm = api.get("calls_per_minute", 30)
    if not isinstance(rpm, int) or rpm < 1 or rpm > 200:
        raise ValueError(f"api.calls_per_minute must be 1-200, got: {rpm}")

    # Batch config
    batch = config.get("batch", {})
    for key in ("phase2", "phase3"):
        if key in batch and not isinstance(batch[key], bool):
            raise ValueError(f"batch.{key} must be true or false, got: {batch[key]}")

    # Concurrency
    conc = config.get("concurrency", {})
    for key in ("max_event_files", "max_extraction_group", "max_enrichment_workers"):
        val = conc.get(key)
        if val is not None and (not isinstance(val, int) or val < 1):
            raise ValueError(
                f"concurrency.{key} must be a positive integer, got: {val}"
            )


def get_paths(
    config: Dict[str, Any], base_dir: Optional[Path] = None
) -> Dict[str, Path]:
    """Get all configured paths as Path objects."""
    if base_dir is None:
        base_dir = Path.cwd()

    paths = {}
    for key, value in config.get("paths", {}).items():
        paths[key] = base_dir / value

    return paths


def get_content_root(paths: Dict[str, Path]) -> Path:
    """Get the directory where book output dirs live.

    Returns paths["content_output"] (output/content/) if it exists or if
    no book dirs exist directly under output_root. Falls back to output_root
    for backwards compatibility with the old flat layout.
    """
    content_output = paths.get("content_output")
    output_root = paths["output_root"]

    # New layout: output/content/ exists and has subdirs
    if content_output and content_output.exists() and any(content_output.iterdir()):
        return content_output

    # Old layout: book dirs directly under output_root
    if _has_book_dirs(output_root):
        return output_root

    # Fresh install or empty: use new layout
    if content_output:
        content_output.mkdir(parents=True, exist_ok=True)
        return content_output

    return output_root


def _has_book_dirs(output_root: Path) -> bool:
    """Check if output_root contains book dirs (not entity dirs)."""
    if not output_root.exists():
        return False
    for d in output_root.iterdir():
        if d.is_dir() and d.name not in ENTITY_DIRS and not d.name.startswith("."):
            if list(d.glob("*-parsed.json")) or list(d.glob("*-event.json")):
                return True
    return False
