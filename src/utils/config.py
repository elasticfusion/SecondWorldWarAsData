"""Configuration file loading and path management."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

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
    """Load configuration from YAML file."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
