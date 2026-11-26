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

import re as _re
from typing import Any, Callable, Dict, List, Tuple

from src.schemas import _version_tuple  # the one version parser


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


# --- bringing a record forward (owner 2026-11-25, D2) -------------------------------------------
# One place every merge / update path asks "can I use this record now?". Forward-compatible steps
# (src/schemas/version_steps, the authoritative upgrade mechanism: TRANSFORMS replaces the unused
# register_upgrade/_UPGRADES above) are applied in memory; a breaking step means the record waits for
# the upgrade release; whatever comes out is checked against the CURRENT schema, so a record the write
# guard let through on its lenient old-version path (types unchecked) is caught here.
#
# Every detail string is safe to log: versions are checked to be digits + a dot before they are
# shown, JSON paths and property names are reduced to a safe charset and capped (a record's keys
# are untrusted text), and no record VALUE is ever included.

CURRENT = "current"
BROUGHT_FORWARD = "brought_forward"
NEEDS_RELEASE_UPGRADE = "needs_release_upgrade"
INVALID = "invalid"
FUTURE = "future"
USABLE = frozenset({CURRENT, BROUGHT_FORWARD})
_VERSION_RE = _re.compile(r"(?:0|[1-9]\d{0,3})\.(?:0|[1-9]\d{0,3})", _re.ASCII)
_SAFE_NAME = _re.compile(r"[A-Za-z0-9_.#-]{1,40}", _re.ASCII)
_MAX_DETAIL = 200


class Forward:
    """The outcome of ``bring_forward``: ``record`` (always a copy, never the caller's object;
    stamped with the current version only when usable), ``status`` and ``detail`` (why it is not
    usable - safe to log, see above). Gate on ``usable``, never on ``record`` being present.
    """

    __slots__ = ("record", "status", "detail")

    def __init__(self, record: Any, status: str, detail: str = "") -> None:
        self.record = record
        self.status = status
        self.detail = detail[:_MAX_DETAIL]

    @property
    def usable(self) -> bool:
        return self.status in USABLE

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Forward(status={self.status!r}, detail={self.detail!r})"


# entity -> validator. Built once per entity; a race only builds one twice (validators are
# stateless for iter_errors), so no lock is needed.
_VALIDATORS: Dict[str, Any] = {}


def _validator(entity: str) -> Any:
    """The current schema's validator for ``entity`` (the write guard's schema). ValueError for an
    entity with no schema: a programming error, never a record outcome."""
    if entity not in _VALIDATORS:
        from jsonschema.validators import validator_for

        from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

        spec = next((s for s in ENTITY_REGISTRY if s.name == entity), None)
        if spec is None:
            raise ValueError(
                f"unknown entity {entity!r}: no schema to bring it forward to"
            )
        schema = load_schema(spec)
        _VALIDATORS[entity] = validator_for(schema)(schema)
    return _VALIDATORS[entity]


def _safe(name: Any) -> str:
    """A path element or property name fit for a log line: an index, or a plain name."""
    if isinstance(name, int):
        return str(name)
    text = str(name)
    return text if _SAFE_NAME.fullmatch(text) else "<key>"


def _extra_keys(instance: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
    """The keys of ``instance`` its schema does not allow (neither a property nor a pattern match)."""
    allowed = schema.get("properties", {})
    patterns = list(schema.get("patternProperties", {}))  # e.g. "^_" metadata keys
    return [
        k
        for k in instance
        if k not in allowed and not any(_re.search(p, str(k)) for p in patterns)
    ]


def _named_fields(error: Any) -> List[str]:
    """The fields a 'required' / 'additionalProperties' error is about: names from the schema for a
    missing field, sanitized record keys (at most 3) for an extra one; [] otherwise."""
    instance = error.instance if isinstance(error.instance, dict) else {}
    if error.validator == "required" and isinstance(error.validator_value, list):
        return [_safe(n) for n in error.validator_value if n not in instance]
    if error.validator == "additionalProperties":
        return [_safe(k) for k in _extra_keys(instance, error.schema)][:3]
    return []


def _error_detail(error: Any) -> str:
    """'<path> <keyword>[ <fields>]' for one validation error."""
    where = "/".join(_safe(p) for p in error.absolute_path) or "<root>"
    names = _named_fields(error)
    detail = f"{where} {error.validator}"
    return f"{detail} {','.join(names)}" if names else detail


def _first_error(entity: str, record: Dict[str, Any]) -> str:
    """'' when ``record`` passes the current schema, else the detail of its first error."""
    error = next(iter(_validator(entity).iter_errors(record)), None)
    return "" if error is None else _error_detail(error)


def _parse_version(raw: Any) -> Any:
    """The version as a tuple, None when absent, or a str saying why it is unusable."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        return "_schema_version is not a string"
    text = raw.strip()
    if not _VERSION_RE.fullmatch(text):
        return "malformed _schema_version"
    return _version_tuple(text)


def _chain(entity: str, version: tuple) -> Any:
    """The steps from ``version`` to the current one, or a Forward-ready (status, detail)."""
    from src.schemas import version_steps

    shown = ".".join(
        str(p) for p in version
    )  # digits + dots only (checked by _parse_version)
    known = {_version_tuple(v) for v in version_steps.history(entity)}
    if version not in known:
        if version < _version_tuple(version_steps.FIRST_VERSION):
            return (
                NEEDS_RELEASE_UPGRADE,
                f"version {shown} predates {version_steps.FIRST_VERSION}",
            )
        return (INVALID, f"version {shown} was never a {entity} version")
    return [s for s in version_steps.steps(entity) if _version_tuple(s[0]) >= version]


def _apply_steps(entity: str, record: Dict[str, Any], chain: list) -> Any:
    """``record`` after the chain's forward steps, or a Forward-ready (status, detail)."""
    from src.schemas import version_steps

    for frm, to, kind in chain:
        if kind == version_steps.BREAKING:
            return (NEEDS_RELEASE_UPGRADE, f"breaking step {frm} -> {to}")
        transform = version_steps.TRANSFORMS.get((entity, frm, to))
        if transform is not None:
            try:
                record = transform(record)
            except (
                Exception
            ) as e:  # noqa: BLE001 - a step that cannot apply: not usable
                return (INVALID, f"step {frm} -> {to}: {type(e).__name__}")
    return record


def _forward_from(entity: str, record: Dict[str, Any], version: Any) -> Forward:
    """bring_forward's work on a copy whose version parsed (None = no version)."""
    from src.schemas import entity_version

    target = entity_version(entity)
    if version is not None:
        if version > _version_tuple(target):
            shown = ".".join(str(p) for p in version)
            return Forward(record, FUTURE, f"version {shown} is newer than {target}")
        stepped = _chain(entity, version)
        if not isinstance(stepped, tuple):
            stepped = _apply_steps(entity, record, stepped)
        if isinstance(stepped, tuple):
            return Forward(record, *stepped)
        record = stepped
    error = _first_error(entity, record)
    if error:
        return Forward(record, INVALID, error)
    status = CURRENT if version == _version_tuple(target) else BROUGHT_FORWARD
    record["_schema_version"] = target
    return Forward(record, status)


def bring_forward(data: Any, entity: str) -> Forward:
    """Bring ``data`` (a record of ``entity``) to the current schema version, in memory.

    * ``current`` - already at the current version and valid;
    * ``brought_forward`` - older (or unversioned), every step forward-compatible, valid after them;
    * ``needs_release_upgrade`` - a breaking step (or a version before per-entity versions) lies
      between it and now: only the upgrade release may change it, and work on it waits;
    * ``invalid`` - not an object, a malformed or undeclared version, nested too deeply to check,
      or it fails the current schema;
    * ``future`` - newer than this code (not understood: never merged into).
    The input is never changed. Raises ValueError only for an entity with no schema."""
    import copy

    _validator(
        entity
    )  # an unknown entity is a programming error: say so before anything else
    if not isinstance(data, dict):
        return Forward(None, INVALID, "not a JSON object")
    version = _parse_version(data.get("_schema_version"))
    try:
        record = copy.deepcopy(data)
        if isinstance(version, str):
            return Forward(record, INVALID, version)
        return _forward_from(entity, record, version)
    except (RecursionError, MemoryError):
        return Forward(None, INVALID, "record too deeply nested or too large to check")


_UNUSABLE_NOTE = {
    INVALID: "the record fails the current schema: it is left out of this merge/update and left "
    "unchanged on S3; fix the field named (or the writer that produced it), then re-run",
    NEEDS_RELEASE_UPGRADE: "the record needs the upgrade release (a breaking schema step): work on "
    "it waits until the release script has run",
    FUTURE: "the record was written by newer code: deploy the current code before processing it",
}


def say_unusable(logger: Any, entity: str, name: str, out: Forward) -> None:
    """The one log line for a record ``bring_forward`` could not make usable (every caller)."""
    if out.usable:
        return
    logger.warning(
        "OBSERV schema.not_usable entity=%s file=%s status=%s detail=%s status_level=warn - %s",
        _safe(entity),
        _re.sub(r"[^A-Za-z0-9_. #-]", "_", str(name), flags=_re.ASCII)[:80],
        out.status,
        out.detail,
        _UNUSABLE_NOTE.get(out.status, ""),
    )
