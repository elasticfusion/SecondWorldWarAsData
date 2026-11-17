"""Startup check that the curated lookup tables the pipeline reads at runtime are present.

Why this exists: every loader of these tables degrades gracefully to "empty" when its file is
missing (by design: a single bad table must not stop a run). The cost is that an OMISSION looks
exactly like a legitimate empty table, so when the ECS image shipped without config/ and data/
(found 2026-11-06), every run used 0 equipment aliases, 0 surname priors and no domain blacklist
for weeks with no warning at all. Per-loader fallbacks cannot tell "absent" from "intentionally
empty"; only a check that knows the tables are REQUIRED can. This is that check.

``REQUIRED_RUNTIME_TABLES`` is the single registry. tests/test_dockerfile_runtime_files.py scans
the pipeline code for config/ and data/ table paths and fails if one is not registered here, so a
new loader cannot reintroduce the silent omission.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# relative path -> top-level YAML key that must be non-empty (None = file must merely parse).
REQUIRED_RUNTIME_TABLES: Dict[str, str | None] = {
    "config/equipment_aliases.yaml": "aliases",
    "config/place_aliases.yaml": "aliases",
    "config/domain_blacklist.yaml": "blacklist",
    "config/award_sources.yaml": "sources",
    "data/surname_frequency.yaml": "surnames",
    "data/unit_nicknames.yaml": "nicknames",
}

# Read at runtime but legitimately absent until first written (learned at runtime).
OPTIONAL_RUNTIME_TABLES = ("config/equipment_aliases_learned.yaml",)


def _problem(path: Path, key: str | None) -> str | None:
    """Why ``path`` is unusable, or None if it is fine."""
    import yaml

    if not path.is_file():
        return "missing"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        return f"unreadable:{type(e).__name__}"
    if not data:
        return "empty"
    if key and not (isinstance(data, dict) and data.get(key)):
        return f"empty_key:{key}"
    return None


def check_runtime_tables(root: Path) -> List[str]:
    """Return ``"<path>=<problem>"`` for every required table that is unusable under ``root``.

    Emits one ``OBSERV runtime.table_unusable status=warn`` per bad table (greppable detail) and a
    single ``OBSERV runtime.tables`` rollup: status=error naming EVERY bad table when any is
    unusable (Slack + email; escalation is rate-limited per scope.event, so the full list must be
    on the one escalated line), status=ok otherwise, so both an omission and a clean pass are
    visible. Never raises: a missing table degrades output quality but must not stop the run.
    """
    from src.utils.observe import observe, observe_warn

    bad: List[str] = []
    for rel, key in REQUIRED_RUNTIME_TABLES.items():
        try:
            why = _problem(root / rel, key)
        except Exception as e:  # noqa: BLE001 - the check itself must never stop a run
            why = f"check_failed:{type(e).__name__}"
        if why:
            bad.append(f"{rel}={why}")
            observe_warn("runtime", "table_unusable", table=rel, problem=why)
    observe(
        "runtime",
        "tables",
        status="error" if bad else "ok",
        required=len(REQUIRED_RUNTIME_TABLES),
        unusable=len(bad),
        # Basenames keep the one escalated line under observe's 200-char field cap; the per-table
        # warn lines above carry the full path + reason.
        tables=",".join(Path(b.split("=")[0]).stem for b in bad) if bad else None,
        note=(
            "loaders silently fall back to empty tables; output quality degraded"
            if bad
            else ""
        ),
    )
    return bad
