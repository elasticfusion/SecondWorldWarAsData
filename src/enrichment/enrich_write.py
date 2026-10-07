"""Single native write path for Phase-3 enrichment.

Enrichment previously wrote entity records via raw json.dump/write_text, bypassing the
schema contract + version stamping that extraction uses — a parallel write path that
produced un-versioned/un-contracted records (the drift this consolidates away).

`save_enriched(path, data, entity)` is the ONE path every enrichment write should use:
  1. (update case) the on-disk record was already read by the caller; stamp + lock-write it.
  2. inject_metadata(data, entity) — stamps the entity's _schema_version + _last_updated.
  3. write_json_with_lock — atomic, lock-guarded (also re-stamps defensively).

For the read-modify-save case, use `update_enriched(path, entity, mutate)` which reads the
existing record under the schema contract (read_for_update: future-schema records skipped,
older best-effort), applies the caller's mutate(record), stamps, and lock-writes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def save_enriched(path: Path, data: Dict[str, Any], entity: str) -> None:
    """Stamp the entity's schema version + atomically lock-write a (new or fully-built)
    enrichment record. Use for records the caller constructed in full."""
    from src.schemas import inject_metadata
    from src.utils.file_lock import write_json_with_lock

    inject_metadata(data, entity=entity)
    write_json_with_lock(path, data)


def update_enriched(
    path: Path,
    entity: str,
    mutate: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
) -> bool:
    """Read an existing entity record under the schema contract, apply `mutate`, stamp, and
    lock-write. Returns True if written, False if skipped (future-schema record) or missing.

    `mutate(record)` edits the record in place (or returns a new one); the schema version is
    stamped automatically after — callers must NOT hand-roll inject_metadata.
    """
    from src.schemas import entity_version
    from src.schemas.schema_contract import read_for_update
    from src.utils.file_lock import locked_json

    if not path.exists():
        return False
    target = entity_version(entity)
    with locked_json(path) as (record, save):
        record, skip = read_for_update(record, target, logger, path.name)
        if skip:
            return False
        result = mutate(record)
        out = result if isinstance(result, dict) else record
        from src.schemas import inject_metadata

        inject_metadata(out, entity=entity)
        save(out)
    return True
