#!/usr/bin/env python3
"""Apply a deploy config PROFILE overlay onto config.yaml (used by scripts/deploy_all.sh).

Profiles live in ``config/profiles/<name>.yaml`` and hold ONLY the settings that define them. They
are merged onto the current ``config.yaml`` — never copied over it. (The old
``cp config.<profile>.yaml config.yaml`` silently no-op'd when the file was absent, and when present
replaced the whole config with a copy 84+ keys stale: dedup, model_map, notification e-mail, ...)

ABSOLUTE over the managed keys: the managed set is the union of keys ANY profile sets. A managed
key the chosen profile does not set is reset to its ``config.yaml.example`` baseline, so switching
review-all-data -> balanced does not leave force_reprocess/DEBUG behind. Unmanaged keys are never
touched.

Fail-loud contract (exit 1, config.yaml untouched): unknown profile; missing overlay / config /
baseline; unreadable YAML; an overlay key absent from config.yaml (stale/misspelled); a type
mismatch (mapping vs scalar) between overlay and config.
On success: writes a 0600 timestamped backup, then config.yaml atomically with its original mode,
and prints every change (values of secret-like keys masked).

Note: re-serialization drops YAML comments from config.yaml (the backup keeps them).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

PROFILES = ("balanced", "cost-optimized", "performance-optimized", "review-all-data")
ROOT = Path(__file__).resolve().parent.parent
_SECRET_RE = re.compile(r"key|token|secret|password|credential", re.IGNORECASE)
_MISSING = object()


class ProfileError(Exception):
    """A profile cannot be applied safely; config.yaml is left untouched."""


def _flatten(
    d: Dict[str, Any], prefix: Tuple[str, ...] = ()
) -> Dict[Tuple[str, ...], Any]:
    out: Dict[Tuple[str, ...], Any] = {}
    for k, v in d.items():
        if isinstance(v, dict) and v:
            out.update(_flatten(v, prefix + (k,)))
        else:
            out[prefix + (k,)] = v
    return out


def _lookup(d: Dict[str, Any], path: Tuple[str, ...]) -> Any:
    node: Any = d
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _load(path: Path, what: str) -> Dict[str, Any]:
    if not path.is_file():
        raise ProfileError(f"{what} missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"unreadable YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ProfileError(f"{path} must be a YAML mapping")
    return data


def _desired(
    name: str, profiles_dir: Path, baseline: Dict[str, Any]
) -> Dict[Tuple[str, ...], Any]:
    """Absolute target for every managed key: the profile's value, else the baseline's."""
    overlays = {
        p: _flatten(_load(profiles_dir / f"{p}.yaml", f"profile overlay {p!r}"))
        for p in PROFILES
    }
    managed = set().union(*overlays.values())
    target: Dict[Tuple[str, ...], Any] = {}
    for path in managed:
        if path in overlays[name]:
            target[path] = overlays[name][path]
        else:
            base = _lookup(baseline, path)
            if base is _MISSING:
                raise ProfileError(
                    f"managed key {'.'.join(path)} missing from config.yaml.example baseline"
                )
            target[path] = base
    return target


def _plan(
    config: Dict[str, Any], target: Dict[Tuple[str, ...], Any]
) -> List[Tuple[str, Any, Any]]:
    unknown, mismatched, changes = [], [], []
    for path, new in sorted(target.items()):
        dotted = ".".join(path)
        old = _lookup(config, path)
        if old is _MISSING:
            unknown.append(dotted)
        elif isinstance(old, dict) != isinstance(new, dict):
            mismatched.append(
                f"{dotted} ({type(old).__name__} vs {type(new).__name__})"
            )
        elif old != new:
            changes.append((dotted, old, new))
    if unknown or mismatched:
        lines = [f"key absent from config.yaml: {k}" for k in unknown]
        lines += [f"type mismatch: {k}" for k in mismatched]
        raise ProfileError(
            "profile cannot be applied safely:\n      - " + "\n      - ".join(lines)
        )
    return changes


def _set(config: Dict[str, Any], dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    node = config
    for part in parents:
        node = node[part]
    node[leaf] = value


def _show(dotted: str, value: Any) -> str:
    return "'***'" if _SECRET_RE.search(dotted.rsplit(".", 1)[-1]) else repr(value)


def _write_atomic(config_path: Path, config: Dict[str, Any]) -> Path:
    """0600 backup, then an atomic replace that keeps config.yaml's original mode."""
    mode = stat.S_IMODE(config_path.stat().st_mode)
    backup = config_path.with_name(
        f"{config_path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    )
    shutil.copyfile(config_path, backup)
    os.chmod(backup, 0o600)
    tmp = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(yaml.safe_dump(config, sort_keys=False))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, config_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return backup


def apply_profile(
    name: str, config_path: Path, profiles_dir: Path, baseline_path: Path
) -> int:
    try:
        if name not in PROFILES:
            raise ProfileError(
                f"unknown profile {name!r} (expected one of {', '.join(PROFILES)})"
            )
        config = _load(config_path, "config.yaml (create it from config.yaml.example)")
        target = _desired(name, profiles_dir, _load(baseline_path, "baseline"))
        changes = _plan(config, target)
    except ProfileError as e:
        print(f"  ✗ {e}\n    config.yaml NOT modified.")
        return 1
    if not changes:
        print(f"  → profile {name!r}: config.yaml already matches (no changes)")
        return 0
    for dotted, _old, new in changes:
        _set(config, dotted, new)
    backup = _write_atomic(config_path, config)
    print(
        f"  → profile {name!r} applied ({len(changes)} change(s); backup: {backup.name})"
    )
    for dotted, old, new in changes:
        print(f"      {dotted}: {_show(dotted, old)} -> {_show(dotted, new)}")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", help=" | ".join(PROFILES))
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--profiles-dir", default=str(ROOT / "config" / "profiles"))
    ap.add_argument("--baseline", default=str(ROOT / "config.yaml.example"))
    a = ap.parse_args(argv)
    return apply_profile(
        a.profile, Path(a.config), Path(a.profiles_dir), Path(a.baseline)
    )


if __name__ == "__main__":
    sys.exit(main())
