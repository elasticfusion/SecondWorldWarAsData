"""What a pipeline task reported about its own stop (task 1.1b, owner 2026-11-18).

A task writes ``taskstop#<task id>`` in the api-cache table as it goes: ``claimed`` (it counted a
Phase 3 attempt), ``given_back`` (it gave that attempt back), ``alerted`` (an operator alert for
its stop was delivered), ``sigterm`` / ``synced`` (it was stopped and whether its emergency upload
worked), ``unsent_event`` / ``unsent_note`` (its own stop alert could not be delivered - e.g. no NAT,
so no route to SNS - and the trigger relays it). DynamoDB is reached through the gateway endpoint,
so this works with no NAT.

When ECS reports the task STOPPED, the trigger Lambda reads the marker
(``trigger_handler._handle_task_stopped_event``): it alerts a stop the task did not report (killed
outright, out of memory, no network for its alert), and it gives back an attempt the task claimed
but could not give back itself after an outside stop (a Spot reclaim, an ECS stop, a deploy).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

TTL_DAYS = 7
FIELDS = (
    "claimed",
    "given_back",
    "alerted",
    "sigterm",
    "synced",
    "book",
    "phase",
    "unsent_event",
    "unsent_note",
)
# with the trigger's relay prefix and its upload sentence, under observe's 300-character note
UNSENT_NOTE_MAX = 160


def key(task_id: str) -> str:
    if not task_id or "#" in task_id or "/" in task_id:
        raise ValueError(f"not a task id: {task_id!r}")
    return f"taskstop#{task_id}"


def mark(table: Any, task_id: str, **fields: Any) -> None:
    """Set ``fields`` (names from FIELDS) on the task's marker, creating it. Raises on AWS errors
    (callers on exit paths catch)."""
    unknown = set(fields) - set(FIELDS)
    if unknown or not fields:
        raise ValueError(f"unknown marker fields: {sorted(unknown)}")
    names = {f"#{n}": n for n in fields}
    values = {f":{n}": v for n, v in fields.items()}
    values[":ttl"] = int(time.time()) + TTL_DAYS * 86400
    table.update_item(
        Key={"cache_key": key(task_id)},
        UpdateExpression="SET "
        + ", ".join(f"#{n} = :{n}" for n in fields)
        + ", #ttl = :ttl",
        ExpressionAttributeNames={**names, "#ttl": "ttl"},
        ExpressionAttributeValues=values,
    )


def read(table: Any, task_id: str) -> Dict[str, Any]:
    """The task's marker fields ({} if it wrote none)."""
    item: Optional[dict] = table.get_item(Key={"cache_key": key(task_id)}).get("Item")
    return {k: v for k, v in (item or {}).items() if k in FIELDS}
