#!/usr/bin/env python3
"""Apply a deploy config PROFILE overlay onto config.yaml (used by scripts/deploy_all.sh).

Profiles live in ``config/profiles/<name>.yaml`` and hold ONLY the settings that define the
profile. They are deep-merged onto the current ``config.yaml`` — never copied over it. (The old
``cp config.<profile>.yaml config.yaml`` silently no-op'd when the file was absent, and when present
replaced the whole config with a copy 84+ keys stale: dedup, model_map, notification e-mail, ...)

Fail-loud contract (exit 1, config.yaml untouched):
  * unknown profile / missing overlay file / unreadable YAML;
  * an overlay key that does not exist in config.yaml (a stale or misspelled key would otherwise
    be silently added and ignored by the code).
On success: writes a timestamped backup, then config.yaml, and prints every changed value.

Note: re-serialization drops YAML comments from config.yaml (the backup keeps them).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

PROFILES = ("balanced", "cost-optimized", "performance-optimized", "review-all-data")


def _overlay(
    base: Dict[str, Any], over: Dict[str, Any], path: str = ""
) -> Tuple[List[str], List[Tuple[str, Any, Any]]]:
    """Deep-merge ``over`` into ``base`` IN PLACE. Returns (unknown_keys, changes)."""
    unknown: List[str] = []
    changes: List[Tuple[str, Any, Any]] = []
    for key, val in over.items():
        dotted = f"{path}{key}"
        if key not in base:
            unknown.append(dotted)
            continue
        if isinstance(val, dict) and isinstance(base[key], dict):
            u, c = _overlay(base[key], val, dotted + ".")
            unknown += u
            changes += c
        elif base[key] != val:
            changes.append((dotted, base[key], val))
            base[key] = val
    return unknown, changes


def apply_profile(name: str, config_path: Path, profiles_dir: Path) -> int:
    if name not in PROFILES:
        print(f"  ✗ unknown profile {name!r} (expected one of {', '.join(PROFILES)})")
        return 1
    overlay_path = profiles_dir / f"{name}.yaml"
    if not overlay_path.is_file():
        print(f"  ✗ profile overlay missing: {overlay_path}")
        return 1
    if not config_path.is_file():
        print(f"  ✗ {config_path} missing (create it from config.yaml.example)")
        return 1
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        print(f"  ✗ unreadable YAML: {e}")
        return 1
    if not isinstance(config, dict) or not isinstance(overlay, dict):
        print("  ✗ config and profile must both be YAML mappings")
        return 1
    unknown, changes = _overlay(config, overlay)
    if unknown:
        print(
            f"  ✗ profile {name!r} sets keys absent from {config_path} (stale/misspelled):"
        )
        for k in unknown:
            print(f"      - {k}")
        print("    config.yaml NOT modified.")
        return 1
    if not changes:
        print(f"  → profile {name!r}: config.yaml already matches (no changes)")
        return 0
    backup = config_path.with_name(
        f"{config_path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(config_path, backup)
    tmp = config_path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    tmp.replace(config_path)
    print(
        f"  → profile {name!r} applied ({len(changes)} change(s); backup: {backup.name})"
    )
    for k, old, new in changes:
        print(f"      {k}: {old!r} -> {new!r}")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", help=" | ".join(PROFILES))
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--profiles-dir", default="config/profiles")
    a = ap.parse_args(argv)
    return apply_profile(a.profile, Path(a.config), Path(a.profiles_dir))


if __name__ == "__main__":
    sys.exit(main())
