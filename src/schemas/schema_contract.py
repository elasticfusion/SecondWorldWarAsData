"""Schema contracts: code declares the schema version it targets; historical objects are
upgraded case-by-case, never force-migrated.

Principle (owner-directed): schemas evolve and are versioned; each piece of code states which
schema version it was written for. When code reads a record written under an OLDER version,
it either applies a *registered* upgrade for that step (added deliberately, case by case) or
flags the record as needing upgrade — it never silently assumes the old shape is fine.

Usage:
    from src.schemas.schema_contract import read_record

    TARGET = "2.24"  # this module targets schema 2.24

    rec, status = read_record(raw, TARGET)
    # status: "ok" (version matches/newer), "upgraded" (a registered upgrade ran),
    #         "needs_upgrade" (older, no upgrader registered — caller decides)

Register an upgrade for a specific version step when (and only when) you decide a historical
object should be brought forward:

    @register_upgrade("2.23", "2.24")
    def _v23_to_v24(rec: dict) -> dict:
        rec.setdefault("member_countries", [])
        return rec
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple


class FutureSchemaError(Exception):
    """Raised when code reads a record written by a NEWER schema than it targets — the code
    was not built to understand it and must fail gracefully rather than silently proceed.
    """


# Registry: (from_version, to_version) -> upgrade function. Populated case-by-case by
# features that choose to bring historical objects forward.
_UPGRADES: Dict[Tuple[str, str], Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def register_upgrade(from_version: str, to_version: str):
    """Decorator registering a case-by-case upgrade for one version step."""

    def _wrap(fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
        _UPGRADES[(from_version, to_version)] = fn
        return fn

    return _wrap


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except (TypeError, ValueError):
        return (0,)


def read_record(
    data: Dict[str, Any], target: str, *, strict: bool = True
) -> Tuple[Dict[str, Any], str]:
    """Return (record, status) for `data` as read by code targeting `target`.

    Statuses:
    - "ok"            — record version == target.
    - "upgraded"      — record older than target AND a registered upgrade for the exact
                        (file_version -> target) step ran.
    - "needs_upgrade" — record older than target, no registered upgrader (caller decides:
                        process best-effort, skip, or queue a future case-by-case upgrader).
    - "future"        — record NEWER than target (written by a schema the code was not built
                        for). With strict=True (default) this raises FutureSchemaError —
                        code not updated for a future schema must not silently proceed. With
                        strict=False it returns (data, "future") so a batch caller can skip
                        + flag the record instead of aborting the whole run.
    """
    file_version = str(data.get("_schema_version", "0.0"))
    fv, tv = _version_tuple(file_version), _version_tuple(target)
    if fv == tv:
        return data, "ok"
    if fv > tv:
        if strict:
            raise FutureSchemaError(
                f"record schema {file_version} is NEWER than this code's target {target}; "
                f"code must be updated to handle it (or read with strict=False to skip)"
            )
        return data, "future"
    upgrader = _UPGRADES.get((file_version, target))
    if upgrader is not None:
        return upgrader(dict(data)), "upgraded"
    return data, "needs_upgrade"


def read_for_update(
    data: Dict[str, Any], target: str, logger, label: str = ""
) -> Tuple[Dict[str, Any], bool]:
    """Batch-safe read for merge/update paths. Returns (record, skip).

    - future record -> logs a WARNING and returns skip=True (the caller must not merge into a
      record it does not understand; it skips that record gracefully instead of crashing).
    - needs_upgrade -> logs a debug note, skip=False (merged best-effort then re-stamped).
    - ok / upgraded -> skip=False.
    Encapsulates the strict=False future-handling so every adopter is a uniform 2-liner.
    """
    rec, status = read_record(data, target, strict=False)
    if status == "future":
        logger.warning(
            "Skipping %s: record schema %s is NEWER than this code's target %s — "
            "code not updated for a future schema",
            label or "record",
            data.get("_schema_version"),
            target,
        )
        return rec, True
    if status == "needs_upgrade":
        logger.debug(
            "Processing %s best-effort: pre-%s record, no registered upgrader",
            label or "record",
            target,
        )
    return rec, False
