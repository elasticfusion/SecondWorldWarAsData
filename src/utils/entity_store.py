"""DEPRECATED: DynamoDB entity (JSON) store — removed.

DynamoDB is no longer used for JSON/entity storage. S3 is the sole authoritative store for
entity records (output/**/*.json); the file-based writers (e.g.
``bibliography.store_bibliography_entry``) write S3-authoritative JSON directly, with a
concurrency-safe deterministic-filename + reconcile scheme (no shared mutable index).

DynamoDB is RETAINED only for QUEUE / COORDINATION (locks, pending queue, NAT lease, batch
tracking, dedup-run claims, reaper, manifests) — those live in src/utils/locks.py and the
trigger Lambda, NOT here.

This module remains importable so existing call sites keep working: ``get_entity_store()``
now always returns ``None``, which every caller already handles by taking the S3/file path.
``DynamoEntityStore`` is a tombstone that raises if anyone tries to instantiate it, so no code
silently reintroduces a Dynamo JSON write.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DynamoEntityStore:  # pragma: no cover - tombstone
    """Removed. DynamoDB is no longer a JSON/entity store (S3 is authoritative). Retained as
    a tombstone to fail loudly if anything tries to reintroduce a Dynamo JSON write."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "DynamoEntityStore is removed — DynamoDB is no longer used for JSON/entity "
            "storage (S3 is authoritative). Use the file-based entity writers."
        )


def get_entity_store() -> Optional["DynamoEntityStore"]:
    """Always None — the Dynamo entity store is removed; callers take the S3/file path."""
    return None
