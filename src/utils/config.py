"""Configuration file loading and path management."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


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
