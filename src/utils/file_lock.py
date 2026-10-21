"""File locking utilities for concurrent access."""

import json
import logging
import platform
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Per-file threading locks (prevents in-process races; flock handles cross-process)
_file_locks: Dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _get_file_lock(filepath: Path) -> threading.Lock:
    """Get or create a threading lock for a specific file path."""
    key = str(filepath.resolve())
    with _file_locks_guard:
        if key not in _file_locks:
            _file_locks[key] = threading.Lock()
        return _file_locks[key]


def _annotate_semantic_flags(filepath: Path, data: Any) -> None:
    """Attach advisory semantic quality flags to an entity record (fail-safe, additive, content
    never altered). Shared by the write guard and the locked_json read-modify-write path so both
    write paths get the same flags. No-op for non-entity files / unknown entities / any error.
    """
    try:
        if not isinstance(data, dict) or _is_non_entity_file(filepath):
            return
        from src.utils.semantic_flags import annotate as _annotate_flags

        _annotate_flags(_resolve_entity_type(filepath), data)
    except Exception:  # noqa: BLE001
        pass


def _atomic_write_json(filepath: Path, data: Any) -> None:
    """Crash-atomic JSON write: serialize fully, temp file in the same dir, fsync, os.replace. Never
    truncate-in-place (a mid-write crash would corrupt the file)."""
    import os as _os
    import tempfile as _tf

    payload = json.dumps(data, indent=2, ensure_ascii=False)
    d = _os.path.dirname(str(filepath)) or "."
    tfd, tname = _tf.mkstemp(dir=d, suffix=".tmp")
    try:
        with _os.fdopen(tfd, "w", encoding="utf-8") as tf:
            tf.write(payload)
            tf.flush()
            _os.fsync(tf.fileno())
        _os.replace(tname, filepath)
    except BaseException as _se:
        if _os.path.exists(tname):
            _os.unlink(tname)
        logger.warning("OBSERV locked_json.save_failed file=%s err=%s", filepath, _se)
        raise


def _guarded_save(filepath: Path, new_data: Any) -> bool:
    """Shared save() body for locked_json: run the SAME central write-guard as write_json_with_lock
    (_validate_entity blocks a schema-invalid record), then stamp metadata at the record's REAL
    per-entity version, then crash-atomic write. Returns False (and does NOT write) on a block — the
    read-modify-write merge paths (equipment/people/places/people_groups/dates/weather/enrich_write/
    batch_parallel) previously called only _annotate_semantic_flags here, bypassing validation so a
    merge that dropped a required field was persisted unguarded."""
    if not _validate_entity(filepath, new_data):
        logger.warning(
            "OBSERV locked_json.blocked_schema_invalid file=%s — merge NOT persisted",
            filepath,
        )
        return False
    # Stamp metadata ONLY for a KNOWN entity record (a non-entity bookkeeping/index file must
    # round-trip unchanged — the pre-guard save() never stamped). _resolve_entity_type returns the
    # parent dir name for ANY path, so gate on membership in ENTITY_SCHEMA_VERSIONS AND exclude
    # non-entity files (index.json/duplicate_report.json/dotfiles) that live inside an entity dir
    # (also avoids a min-version mis-stamp for an unknown dir, cf. the #498 unknown-entity guard).
    entity = _resolve_entity_type(filepath)
    try:
        from src.schemas import ENTITY_SCHEMA_VERSIONS, inject_metadata

        if entity in ENTITY_SCHEMA_VERSIONS and not _is_non_entity_file(filepath):
            inject_metadata(new_data, entity=entity)
    except Exception as e:  # noqa: BLE001 - stamping must never block a validated write
        logger.debug("locked_json metadata stamp skipped for %s: %s", filepath, e)
    _atomic_write_json(filepath, new_data)
    return True


@contextmanager
def locked_json(filepath: Path):
    """Context manager that holds an exclusive lock across read-modify-write.

    Usage:
        with locked_json(path) as (data, save):
            data["events"].append(new_event)
            save(data)

    If the file doesn't exist, data is an empty dict.
    Uses threading lock (in-process) + flock (cross-process) for full safety.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _get_file_lock(filepath)
    system = platform.system()

    thread_lock.acquire()
    try:
        if system in ("Linux", "Darwin"):
            import fcntl

            # Open in r+ if exists, else create
            if filepath.exists():
                f = open(filepath, "r+", encoding="utf-8")
            else:
                f = open(filepath, "w+", encoding="utf-8")

            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0)
                content = f.read()
                data = json.loads(content) if content.strip() else {}

                def save(new_data):
                    return _guarded_save(filepath, new_data)

                yield data, save
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                f.close()
        else:
            # Fallback: threading lock only (no cross-process safety)
            data = {}
            if filepath.exists():
                with open(filepath, encoding="utf-8") as f:
                    data = json.load(f)

            def save(new_data):
                return _guarded_save(filepath, new_data)

            yield data, save
    finally:
        thread_lock.release()


# Required fields per entity type (directory name → field list)
_REQUIRED_FIELDS: Dict[str, list] = {
    "people": ["PersonID", "name"],
    "people_groups": ["GroupID", "group_name"],
    "places": ["PlaceID"],
    "dates": ["DateID", "date_start"],
    "equipment": ["EquipmentID", "common_name"],
    "weather": ["WeatherID"],
    "casualties": ["CasualtyID"],
    "logistics": ["LogisticsID"],
}


def _repair_empty_ulids(data: Any) -> None:
    """Recursively replace EMPTY-STRING values on ``*ID``/``*_id`` keys with a fresh ULID,
    in place. Only empty strings are touched — never a non-empty value (so real/merged IDs are
    preserved) and never ``None`` (nullable fields keep their null). This repairs exactly the
    empty-ULID write bug without the merge-breaking side effect of regenerating malformed IDs.
    """
    import ulid

    if isinstance(data, dict):
        for key, value in data.items():
            if (
                (key.endswith("ID") or key.endswith("_id"))
                and isinstance(value, str)
                and value == ""
            ):
                data[key] = str(ulid.new())
            elif isinstance(value, (dict, list)):
                _repair_empty_ulids(value)
    elif isinstance(data, list):
        for item in data:
            _repair_empty_ulids(item)


import re as _re_mod

_ULID_RE = _re_mod.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _repair_primary_key(entity_type: str, data: Dict[str, Any], filepath: Path) -> None:
    """Mint a fresh ULID for an entity's PRIMARY-KEY field when it is missing / None / empty /
    not a valid ULID. The PK is the record's own identity (locally generated, never a reference),
    so this self-heal is lossless and needs no reanalysis — it applies uniformly to EVERY
    schema-enforced entity via its registry ``required_id``. Reference IDs are untouched.
    """
    try:
        import ulid

        from src.schemas.entity_registry import ENTITY_REGISTRY

        spec = next((s for s in ENTITY_REGISTRY if s.name == entity_type), None)
        if spec is None or not spec.required_id:
            return
        pk = spec.required_id
        val = data.get(pk)
        if not isinstance(val, str) or not _ULID_RE.match(val):
            data[pk] = str(ulid.new())
            logger.warning(
                "Auto-repaired %s %s (%s): minted a fresh ULID for a missing/invalid "
                "primary key (self-identity — no reanalysis needed)",
                entity_type,
                pk,
                filepath.name,
            )
    except Exception:  # noqa: BLE001 - best-effort; never block on the repair itself
        pass


def _validate_entity(
    filepath: Path, data: Dict[str, Any], record_stats: bool = True
) -> bool:
    """Central write-time guard. Returns True if the write should proceed.

    Resolves the entity from the output path, repairs empty/None/invalid ULID fields, then
    jsonschema-validates against the enforced schema (version-aware). On a genuine schema
    violation it LOGS (path + validator keyword only, never record values) and returns False
    (BLOCK the write). Fail-safe: returns True when the entity/schema/validator is unavailable
    or the file is a non-entity file. Validation is UNCONDITIONAL — there is no opt-out env.

    ``record_stats``: when False, outcomes are NOT fed to the validation-stats catcher — used by
    pre-write checks so the subsequent real write is the single recording point (no double-count).
    """
    if _is_non_entity_file(filepath):
        return True
    entity_type = _resolve_entity_type(filepath)

    try:
        _repair_empty_ulids(data)
    except Exception:  # noqa: BLE001 - repair is best-effort
        pass

    # Attach advisory semantic quality flags (ocean coords, out-of-window dates, unresolvable
    # citations, ...). Additive + fail-safe: flags are observability only — they never alter
    # content fields and never change this guard's allow/block decision.
    _annotate_semantic_flags(filepath, data)

    # Centralized, schema-consistent result de-duplication (RESULT_DEDUP_DESIGN.md):
    # collapse duplicate result-list entries by SOURCE-URL identity (upsert: refresh
    # checked_at + safe-merge, never downgrade; different URLs preserved = corroboration)
    # uniformly for every entity. Additive + fail-safe (never raises, leaves data intact
    # on error) — so "don't repeat JSON results" behaves identically across all schemas.
    try:
        from src.utils.result_dedup import dedup_entity as _dedup_entity

        if entity_type:
            _dedup_entity(data, entity_type)
    except Exception:  # noqa: BLE001 - dedup must never block a write
        pass

    schema = _schema_for_entity(entity_type, filepath, data)
    if schema is None:
        return (
            True  # not a schema-enforced entity (fail-safe) — legacy warn already done
        )

    # Auto-repair a missing/empty/malformed PRIMARY-KEY ULID. The PK (PersonID/PlaceID/…) is the
    # record's OWN identity, minted locally (str(ulid.new())) — it is NOT a reference to another
    # record, so synthesizing one loses nothing and orphans nothing, and needs NO reanalysis of
    # the record. This heals PK-corrupt records (incl. legacy fragments) in-place across ALL
    # features instead of blocking them. Reference IDs (EventID/Sub_eventID/cross-ref MentionIDs)
    # are deliberately NOT regenerated here (that would orphan the link) — only the entity's own
    # required_id primary key.
    _repair_primary_key(entity_type, data, filepath)

    status, target = _version_status(data, entity_type)
    if status == "future":
        logger.warning(
            "Allowing %s write (%s) AS-IS: record schema %s is NEWER than target %s",
            entity_type,
            filepath.name,
            data.get("_schema_version"),
            target,
        )
        if record_stats:
            _record_stat(entity_type, "allow", None, "future")
        return True

    return _run_schema_validation(
        data, schema, entity_type, filepath, status, target, record_stats
    )


def _record_stat(
    entity: str, outcome: str, keyword: Optional[str], version_state: str
) -> None:
    """Forward a guard outcome to the validation-stats catcher. Fail-safe: telemetry must never
    break the guard, so any import/record error is swallowed."""
    try:
        from src.utils.validation_stats import record_validation

        record_validation(entity, outcome, keyword, version_state)
    except Exception:  # noqa: BLE001
        pass


def _record_record_quality(
    entity: str, data: Dict[str, Any], version_state: str
) -> None:
    """Forward low-information + semantic-flag observations for an ALLOWED record to the catcher
    (allow_empty / allow_flagged). Fail-safe: telemetry must never break the guard."""
    try:
        from src.utils.validation_stats import record_record_quality

        record_record_quality(entity, data, version_state)
    except Exception:  # noqa: BLE001
        pass


def _is_non_entity_file(filepath: Path) -> bool:
    """True for metadata/index/report/tracking files that carry no entity schema."""
    return filepath.name in (
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "not_people.json",
        "not_related.json",
        "related_groups_report.json",
    ) or filepath.name.startswith(".")


def _resolve_entity_type(filepath: Path) -> str:
    """Entity type from the path. Events are the exception: they live at
    output/content/<Book>/<chapter>-event.json (parent is the BOOK), so resolve them by their
    '-event.json' filename suffix rather than the parent dir."""
    if filepath.name.endswith("-event.json"):
        return "events"
    return filepath.parent.name


def _schema_for_entity(entity_type: str, filepath: Path, data: Dict[str, Any]):
    """Return the enforced schema dict for an entity, or None if it is not a schema-enforced
    entity (in which case a legacy required-field warning is emitted). Fail-safe: None on error.
    """
    try:
        from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

        spec = next((s for s in ENTITY_REGISTRY if s.name == entity_type), None)
        if spec is None:
            required = _REQUIRED_FIELDS.get(entity_type)
            if required:
                missing = [f for f in required if not data.get(f)]
                if missing:
                    logger.warning(
                        "Entity validation: %s missing required fields %s",
                        filepath.name,
                        missing,
                    )
            return None
        return load_schema(spec)
    except Exception:  # noqa: BLE001 - registry/schema unavailable -> allow write
        return None


def _version_status(data: Dict[str, Any], entity_type: str):
    """Resolve the version-aware status via schema_contract, applying the version-less rule.

    Returns (status, target). On 'upgraded', `data` is updated in place with the upgraded
    record so the upgraded form is what gets persisted. A record with NO declared
    _schema_version is treated as 'ok' (strict) — it is malformed, not legitimately old.
    """
    try:
        from src.schemas import entity_version
        from src.schemas.schema_contract import read_record

        target = entity_version(entity_type)
        record, status = read_record(data, target, strict=False)
    except Exception:  # noqa: BLE001 - contract unavailable -> treat as current ("ok")
        return "ok", ""

    declared_version = str(data.get("_schema_version", "")).strip()
    if status == "needs_upgrade" and not declared_version:
        status = "ok"
    if status == "upgraded" and record is not data:
        data.clear()
        data.update(record)
    return status, target


def _has_identity_floor(entity_type: str, data: Dict[str, Any]) -> bool:
    """The non-negotiable identity invariant that applies to EVERY schema version: the entity's
    primary-key ID must be present + non-empty. A record missing its PK is corrupt regardless of
    version (the PK was never a newly-added field), so the lenient 'needs_upgrade' path must NOT
    excuse it. Returns True if the identity floor holds (or the entity has no required_id).
    """
    try:
        from src.schemas.entity_registry import ENTITY_REGISTRY

        spec = next((s for s in ENTITY_REGISTRY if s.name == entity_type), None)
        if spec is None or not spec.required_id:
            return True
        return bool(data.get(spec.required_id))
    except (
        Exception
    ):  # noqa: BLE001 - fail-safe: don't let the floor-check itself block
        return True


def _run_schema_validation(
    data: Dict[str, Any],
    schema: Dict[str, Any],
    entity_type: str,
    filepath: Path,
    status: str,
    target: str,
    record_stats: bool = True,
) -> bool:
    """jsonschema-validate; block on violation unless the record legitimately predates a
    current constraint (needs_upgrade). Logs only the JSON path + validator keyword (no values).
    """
    import jsonschema

    try:
        jsonschema.validate(data, schema)
        # Cross-field consistency jsonschema can't express: bibliography alt_title must be
        # identical at top-level and inside citation (the two copies must never drift).
        if entity_type == "bibliography" and isinstance(data, dict):
            cit = data.get("citation")
            if isinstance(cit, dict) and data.get("alt_title") != cit.get("alt_title"):
                logger.error(
                    "BLOCKED %s write (%s): const at alt_title (top-level != citation.alt_title)",
                    entity_type,
                    filepath.name,
                )
                if record_stats:
                    _record_stat(entity_type, "block", "const", status)
                return False
        if record_stats:
            _record_stat(entity_type, "allow", None, status)
            _record_record_quality(entity_type, data, status)
        return True
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        reason = f"{e.validator} at {loc}"
        if status == "needs_upgrade" and _has_identity_floor(entity_type, data):
            logger.warning(
                "Allowing pre-%s %s write (%s) despite current-schema violation (%s): "
                "no registered upgrader — needs targeted reprocess/upgrade",
                target,
                entity_type,
                filepath.name,
                reason,
            )
            if record_stats:
                _record_stat(
                    entity_type, "allow_warn", str(e.validator), "needs_upgrade"
                )
                _record_record_quality(entity_type, data, "needs_upgrade")
            return True
        logger.error(
            "BLOCKED schema-invalid %s write (%s): %s",
            entity_type,
            filepath.name,
            reason,
        )
        if record_stats:
            _record_stat(entity_type, "block", str(e.validator), status)
        return False
    except Exception:  # noqa: BLE001 - validator error -> fail-safe allow
        return True


def _legacy_required_check(filepath: Path, data: Dict[str, Any]) -> None:
    """Deprecated: superseded by the schema-aware _validate_entity guard."""
    # Skip non-entity files (metadata, indexes, reports, tracking)
    if filepath.name in (
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        "not_people.json",
        "not_related.json",
    ) or filepath.name.startswith("."):
        return
    entity_type = filepath.parent.name
    required = _REQUIRED_FIELDS.get(entity_type)
    if not required:
        return
    missing = [f for f in required if not data.get(f)]
    if missing:
        logger.warning(
            "Entity validation: %s missing required fields %s",
            filepath.name,
            missing,
        )


def write_json_with_lock(
    filepath: Path, data: Dict[str, Any], entity: Optional[str] = None
) -> bool:
    """Write JSON file with file locking for concurrent access. Returns True if the record was
    written, False if the central guard BLOCKED it (schema-invalid). Callers may gate follow-on
    work (e.g. metrics) on the return value instead of a separate pre-check.

    `entity` selects the per-entity schema version to stamp. When omitted, the
    stamp falls back to the MIN version across entities — which SILENTLY DOWNGRADES
    records whose true entity version is above the min (people_groups/equipment at
    2.25). Callers that know their entity MUST pass it so the stamp lands at the
    record's real version (otherwise needs_migration() stays True forever).
    """
    from src.schemas import inject_metadata

    # Validate FIRST, on the record's TRUE incoming _schema_version — the version-aware guard
    # (read_record) must see the real version to apply upgrades / allow legitimately-old records.
    # Stamping BEFORE validation would overwrite the incoming version with current and defeat
    # that logic (an old record would be judged against current and wrongly blocked). Only after
    # the guard approves do we stamp the current version + last-updated.
    if not _validate_entity(filepath, data):
        # Schema-invalid record — do NOT persist it (guard logged the reason).
        return False
    inject_metadata(data, entity=entity)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # Disk space check (local mode only — skip in /tmp/pipeline ECS workdir)
    if not str(filepath).startswith(
        "/tmp/"
    ):  # nosec B108 -- path prefix check, not temp-file use
        import shutil

        free = shutil.disk_usage(filepath.parent).free
        if free < 50 * 1024 * 1024:  # 50MB threshold
            raise OSError(
                f"Low disk space ({free // 1024 // 1024}MB free) — aborting write to {filepath.name}"
            )

    # Atomic write: write to temp file, then replace (crash-safe)
    import tempfile

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=filepath.parent, suffix=".tmp", prefix=filepath.stem
    )
    try:
        import os as _os

        with _os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _os.replace(tmp_path, filepath)
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return True


# Phase-1 parse: a chapter is useless with no readable paragraphs. Below this -> allow_empty flag
# (still written — an empty plate/figure page can be legitimate), never a block.
_PARSED_MIN_PARAGRAPHS = 1


def _usable_paragraph_count(data: Dict[str, Any]) -> int:
    paras = data.get("paragraphs") if isinstance(data, dict) else None
    if not isinstance(paras, list):
        return 0
    return sum(
        1
        for p in paras
        if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()
    )


def guard_parsed_content(name: str, data: Dict[str, Any]) -> bool:
    """Validate + flag Phase-1 parsed content (shared by the local AND S3/Lambda writers so there
    is ONE validation definition). Returns False if schema-INVALID (caller must NOT persist it),
    True otherwise. Records block/allow/allow_empty to the catcher under entity ``parsed``. ``name``
    is a basename for the log (never content). Fail-safe: a validator/import error returns True
    (never lose a parse to a telemetry bug)."""
    try:
        import jsonschema

        from src.schemas.parsed_output import PARSED_OUTPUT_SCHEMA

        try:
            jsonschema.validate(data, PARSED_OUTPUT_SCHEMA)
        except jsonschema.ValidationError as e:
            loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
            logger.error(
                "BLOCKED invalid parsed output (%s): %s at %s", name, e.validator, loc
            )
            _record_stat("parsed", "block", str(e.validator), "ok")
            return False
        if _usable_paragraph_count(data) < _PARSED_MIN_PARAGRAPHS:
            _record_stat("parsed", "allow_empty", "below_floor", "ok")
        else:
            _record_stat("parsed", "allow", None, "ok")
    except Exception:  # noqa: BLE001 - validation/telemetry must never lose a parse
        pass
    return True


def write_parsed_with_guard(filepath: Path, data: Dict[str, Any]) -> bool:
    """Guarded LOCAL writer for Phase-1 ``*-parsed.json`` (HIGH-4b): guard_parsed_content() then
    atomic write. Returns True if written, False if BLOCKED (garbage not persisted). The S3/Lambda
    path calls guard_parsed_content() directly before its own storage.write_json — same validation.
    Deliberately omits the low-disk pre-check + flock of write_json_with_lock (single producer per
    parsed path, re-parse idempotent → entity-grade locking unnecessary)."""
    if not guard_parsed_content(filepath.name, data):
        return False

    filepath.parent.mkdir(parents=True, exist_ok=True)
    import os as _os
    import tempfile

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=filepath.parent, suffix=".tmp", prefix=filepath.stem
    )
    try:
        with _os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        _os.replace(tmp_path, filepath)
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise
    return True


def read_json_with_lock(filepath: Path) -> Dict[str, Any]:
    """Read JSON file with file locking for concurrent access."""
    if not filepath.exists():
        return {}

    system = platform.system()

    if system in ("Linux", "Darwin"):  # Unix-like
        import fcntl

        with open(filepath, encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    elif system == "Windows":
        import msvcrt  # type: ignore[import]

        with open(filepath, encoding="utf-8") as f:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            try:
                return json.load(f)
            finally:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        # Fallback: no locking
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
