"""Per-document lifecycle state (M1 / CONCURRENCY_AND_NAT_SPEC §8).

Each source document tracked as a DynamoDB record `doc#{doc_id}` carrying its
lifecycle status. The dispatcher's enumerate_pending reads these; the pre-stage
and phase tasks advance them. States (forward-only except retry):

    held_unprocessed -> expanding -> routed -> ocr -> parsed
                     -> extracted -> deduped -> enriched -> done
    (or failed / needs-review as terminal-ish off-ramps)

`enumerate_pending` dispatches only docs that are neither terminal (done/failed/
needs-review) nor already in-flight — idempotent across restarts (§8).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Canonical state ordering (index = progress). Off-ramps live outside the list.
STATES: List[str] = [
    "held_unprocessed",
    "expanding",
    "routed",
    "ocr",
    "parsed",
    "extracted",
    "deduped",
    "enriched",
    "done",
]
TERMINAL = {"done", "failed", "needs-review"}
# In-flight = anything past routed and not terminal (a task is actively working it).
IN_FLIGHT = {"expanding", "ocr", "parsed", "extracted", "deduped", "enriched"}


def _table():
    import boto3

    region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    name = os.getenv("CACHE_TABLE", f"{os.getenv('ENV_NAME', 'dev')}-wwii-api-cache")
    return boto3.resource("dynamodb", region_name=region).Table(name)


def _key(doc_id: str) -> str:
    return f"doc#{doc_id}"


def upsert(
    doc_id: str,
    *,
    status: str = "held_unprocessed",
    book: str = "",
    media_type: str = "",
    track: str = "",
    next_phase: str = "phase1",
    source_path: str = "",
) -> None:
    """Create or overwrite a doc lifecycle record. Idempotent."""
    _table().put_item(
        Item={
            "cache_key": _key(doc_id),
            "doc_id": doc_id,
            "status": status,
            "book": book,
            "media_type": media_type,
            "track": track,
            "next_phase": next_phase,
            "source_path": source_path,
            "updated_at": int(time.time()),
        }
    )
    logger.info("doc %s -> %s (track=%s)", doc_id, status, track)


def set_status(doc_id: str, status: str, *, next_phase: Optional[str] = None) -> None:
    """Advance (or off-ramp) a doc's status. next_phase optionally updated."""
    expr = "SET #s = :s, updated_at = :t"
    vals: Dict[str, Any] = {":s": status, ":t": int(time.time())}
    if next_phase is not None:
        expr += ", next_phase = :p"
        vals[":p"] = next_phase
    _table().update_item(
        Key={"cache_key": _key(doc_id)},
        UpdateExpression=expr,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=vals,
    )
    logger.info("doc %s -> %s", doc_id, status)


def get(doc_id: str) -> Optional[Dict[str, Any]]:
    resp = _table().get_item(Key={"cache_key": _key(doc_id)})
    return resp.get("Item")


def is_dispatchable(record: Dict[str, Any]) -> bool:
    """True if a doc should be dispatched: not terminal and not in-flight (§8)."""
    status = record.get("status", "")
    return status not in TERMINAL and status not in IN_FLIGHT


def list_dispatchable() -> List[Dict[str, Any]]:
    """Scan all doc# records and return those that are dispatchable, FIFO by id."""
    table = _table()
    out: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {
        "FilterExpression": "begins_with(cache_key, :p)",
        "ExpressionAttributeValues": {":p": "doc#"},
    }
    while True:
        resp = table.scan(**kwargs)
        out.extend(r for r in resp.get("Items", []) if is_dispatchable(r))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    out.sort(key=lambda r: r.get("doc_id", ""))
    return out
