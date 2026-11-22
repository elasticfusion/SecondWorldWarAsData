"""Pointers from a merged-away record id to the record that absorbed it (owner 2026-11-21).

When two records are combined (the bibliography reconcile folds records citing the same footnote;
the dedup step merges duplicate people, places, units, equipment), one id survives and the other
record is deleted. Anything that kept the old id (a review-queue find, a pull-list item, a saved
search, a link on the site) must still reach the record. This module holds ONE pointer per
merged-away id, in its own DynamoDB table ``{env}-wwii-id-aliases`` (pointers only, never record
JSON):

    alias_key = "<entity>#<old id>"   ->   survivor_id, entity, source, merged_at

``record`` writes a pointer before the old record is deleted (a caller that cannot store it keeps
the old record, so an id always resolves); ``resolve`` follows pointers to the current id (a
survivor can itself be merged later). Local and test runs keep the pointers in
``output/_id_aliases.jsonl``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.schemas import ULID_REGEX

logger = logging.getLogger(__name__)

ENTITIES = frozenset(
    {"bibliography", "people", "people_groups", "places", "equipment", "dates"}
)
MAX_HOPS = 25  # a chain longer than this is a cycle or corruption: said, not followed
_LOCAL_FILE = Path("output/_id_aliases.jsonl")
_local_lock = threading.Lock()
_local: Optional[Dict[str, str]] = None
_TABLE: Dict[str, Any] = {}  # one handle per table name (a merge stores thousands)


def _table_name() -> str:
    """The pointer table: ``ID_ALIAS_TABLE``, else the cache table's name with ``api-cache``
    replaced (``dev-wwii-api-cache`` -> ``dev-wwii-id-aliases``)."""
    explicit = os.environ.get("ID_ALIAS_TABLE", "").strip()
    if explicit:
        return explicit
    cache = os.environ.get("CACHE_TABLE", "").strip()
    if not cache:
        from src.utils.config import load_config

        cache = load_config().get("aws", {}).get("cache_table", "dev-wwii-api-cache")
    return cache.rsplit("api-cache", 1)[0] + "id-aliases"


def _table() -> Any:
    """The DynamoDB pointer table, or None in a local/test run (then the JSONL file is used)."""
    in_lambda = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
    if not in_lambda:
        from src.utils.config import load_config

        if not load_config().get("aws", {}).get("enabled"):
            return None
    name = _table_name()
    if name not in _TABLE:
        from src.utils.aws_clients import get_dynamo_table

        _TABLE[name] = get_dynamo_table(name)
    return _TABLE[name]


def _key(entity: str, record_id: str) -> str:
    return f"{entity}#{record_id}"


def _valid(entity: str, *ids: str) -> bool:
    return entity in ENTITIES and all(
        isinstance(i, str) and ULID_REGEX.fullmatch(i) for i in ids
    )


def _local_pointers() -> Dict[str, str]:
    global _local
    if _local is None:
        _local = {}
        if _LOCAL_FILE.exists():
            for line in _LOCAL_FILE.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    _local[row["alias_key"]] = row["survivor_id"]
                except (ValueError, KeyError, TypeError):
                    continue
    return _local


def record(entity: str, old_id: str, survivor_id: str, source: str) -> bool:
    """Store that ``old_id`` was merged into ``survivor_id``. Returns whether the old id now
    leads to the survivor (a caller deletes the old record only then). A pointer is written once:
    an existing pointer is kept, and is a success when its chain already leads to the survivor (a
    survivor that was itself merged later: A -> S1 -> N). One that leads elsewhere, one that would
    close a cycle, or one that cannot be checked (the survivor's pointers unreadable) is refused.
    Never raises; a failure is an error naming what to check. Merges are serial (one reconcile,
    one dedup at a time); two opposite merges at once are caught later by ``resolve``.
    """
    if not _valid(entity, old_id, survivor_id) or old_id == survivor_id:
        logger.error(
            "OBSERV id_aliases.refused entity=%r old=%r survivor=%r status=error - not a valid "
            "merge pointer (unknown entity, not a ULID, or an id pointing at itself): the "
            "merge's caller passed a bad id",
            str(entity)[:30],
            str(old_id)[:30],
            str(survivor_id)[:30],
        )
        return False
    try:
        return _record_checked(_table(), entity, old_id, survivor_id, source)
    except Exception as e:  # noqa: BLE001 - a merge must never crash on its pointer
        if "ConditionalCheckFailed" in f"{type(e).__name__} {e}":
            return _after_concurrent_write(entity, old_id, survivor_id)
        logger.error(
            "OBSERV id_aliases.write_failed entity=%s old=%s err=%s status=error - the "
            "merged-away record is kept until its pointer is stored. Check the task role's "
            "DynamoDB access to %s (it is created by cloudformation/storage.yaml)",
            entity,
            old_id,
            type(e).__name__,
            _safe_name(),
        )
        return False


def _record_checked(
    table: Any, entity: str, old_id: str, survivor_id: str, source: str
) -> bool:
    head = resolve(entity, survivor_id)
    if head is None:
        return _refuse(
            entity, old_id, survivor_id, "the survivor's pointers are unreadable"
        )
    if head == old_id:
        return _refuse(entity, old_id, survivor_id, "it would close a cycle")
    current = _lookup(table, entity, old_id, consistent=True)
    if current is None:
        _store(table, entity, old_id, survivor_id, source)
        return True
    if _leads_to(entity, old_id, current, survivor_id):
        return (
            True  # stored before (a re-run), or its chain already reaches the survivor
        )
    return _refuse(entity, old_id, survivor_id, f"it already leads to {current!r}")


def _leads_to(entity: str, old_id: str, current: str, survivor_id: str) -> bool:
    """The stored pointer is the same one, or both ids end at the same record."""
    if current == survivor_id:
        return True
    end = resolve(entity, old_id)
    return end is not None and end == resolve(entity, survivor_id)


def _after_concurrent_write(entity: str, old_id: str, survivor_id: str) -> bool:
    """Another writer stored this pointer between the check and the write: a success only when
    it leads to the same survivor."""
    try:
        current = _lookup(_table(), entity, old_id, consistent=True)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "OBSERV id_aliases.read_failed entity=%s id=%s err=%s status=error - check read "
            "access to %s",
            entity,
            old_id,
            type(e).__name__,
            _safe_name(),
        )
        return False
    if current is not None and _leads_to(entity, old_id, current, survivor_id):
        return True
    return _refuse(
        entity, old_id, survivor_id, f"a concurrent merge stored {current!r}"
    )


def _refuse(entity: str, old_id: str, survivor_id: str, why: str) -> bool:
    logger.error(
        "OBSERV id_aliases.conflict entity=%s old=%s survivor=%s status=error - pointer not "
        "stored: %s. The merged-away record is kept; inspect both records and the %s item",
        entity,
        old_id,
        survivor_id,
        why[:120],
        _safe_name(),
    )
    return False


def _store(table: Any, entity: str, old_id: str, survivor_id: str, source: str) -> None:
    """Write one new pointer (write-once in DynamoDB: never over another)."""
    row: Dict[str, Any] = {
        "alias_key": _key(entity, old_id),
        "entity": entity,
        "old_id": old_id,
        "survivor_id": survivor_id,
        "source": str(source)[:60],
        "merged_at": int(time.time()),
    }
    if table is not None:
        table.put_item(Item=row, ConditionExpression="attribute_not_exists(alias_key)")
        return
    with _local_lock:
        _LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOCAL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        _local_pointers()[_key(entity, old_id)] = survivor_id


def _safe_name() -> str:
    try:
        return _table_name()
    except Exception:  # noqa: BLE001
        return "the id-aliases table"


def _lookup(
    table: Any, entity: str, record_id: str, consistent: bool = False
) -> Optional[str]:
    if table is None:
        return _local_pointers().get(_key(entity, record_id))
    item = table.get_item(
        Key={"alias_key": _key(entity, record_id)}, ConsistentRead=consistent
    ).get("Item")
    return str(item["survivor_id"]) if item and item.get("survivor_id") else None


def resolve(entity: str, record_id: str) -> Optional[str]:
    """The current id of a record: ``record_id`` itself when it was never merged, else the record
    that (finally) absorbed it. None when the pointers could not be read or form a cycle (said):
    the caller must not guess."""
    if not _valid(entity, record_id):
        return None
    try:
        table = _table()
        current, seen = record_id, {record_id}
        for _ in range(MAX_HOPS):
            nxt = _lookup(table, entity, current)
            if not nxt:
                return current
            if nxt in seen:
                break
            seen.add(nxt)
            current = nxt
        logger.error(
            "OBSERV id_aliases.cycle entity=%s id=%s status=error - the merge pointers loop or "
            "run longer than %d hops; inspect the %s items for this id",
            entity,
            record_id,
            MAX_HOPS,
            _safe_name(),
        )
        return None
    except Exception as e:  # noqa: BLE001
        logger.error(
            "OBSERV id_aliases.read_failed entity=%s id=%s err=%s status=error - check read "
            "access to %s",
            entity,
            record_id,
            type(e).__name__,
            _safe_name(),
        )
        return None


def reset_local() -> None:
    """Forget the in-process copy of the local pointer file and the table handles (tests)."""
    global _local
    _local = None
    _TABLE.clear()
