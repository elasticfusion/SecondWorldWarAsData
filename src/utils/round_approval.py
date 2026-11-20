"""Human approval to run more batch rounds than the soft cap (owner 2026-11-20).

A run that keeps queueing requests past ``batch.max_rounds`` is stopped (``batch_rounds``): a runaway
must be contained. A human who has looked at the alert can allow more:
``python3 scripts/approve_batch_rounds.py --phase phase3 --scope <scope> --extra N``. That writes the
row read here; every run of that phase and scope gets ``cap + N`` rounds until the row expires (7
days), so an old approval cannot quietly raise a later run's limit. Only an operator may write the
row: the task, Lambda and dispatcher roles are denied ``rounds#*`` writes (cloudformation/iam.yaml).
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

APPROVAL_TTL_S = 7 * 86400
MAX_EXTRA = 100


def _table() -> Optional[Any]:
    name = os.environ.get("CACHE_TABLE", "")
    if not name:
        return None  # no AWS table (local run): no approvals
    import boto3

    dynamodb: Any = boto3.resource(
        "dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    return dynamodb.Table(name)


SCOPE_RE = re.compile(r"^[A-Za-z0-9._,-]{1,150}$")


def row_key(phase: str, scope: str) -> str:
    return f"rounds#{phase}#{scope}"[:200]


def approved_extra(phase: str, scope: str) -> int:
    """Extra rounds a human approved for this phase and scope (0: none, expired, or unreadable)."""
    try:
        table = _table()
        if table is None:
            return 0
        item = table.get_item(Key={"cache_key": row_key(phase, scope)}).get("Item")
        if not item or int(item.get("expires", 0)) < time.time():
            return 0
        return max(0, min(int(item.get("extra", 0)), MAX_EXTRA))
    except (
        Exception
    ) as e:  # noqa: BLE001 - an unreadable approval is "none", said loudly
        logger.error(
            "OBSERV batch.approval_unreadable phase=%s scope=%s err=%s status=error - the round "
            "cap cannot be raised until the approval row can be read (check the table and "
            "dynamodb:GetItem)",
            phase,
            scope,
            type(e).__name__,
        )
        return 0


def approve(phase: str, scope: str, extra: int, who: str = "") -> int:
    """Record the approval (replacing an earlier one). Returns the extra rounds written."""
    if not SCOPE_RE.fullmatch(scope or ""):
        raise ValueError(
            f"scope {scope!r} is not a book name (letters, digits . _ , -)"
        )
    extra = max(1, min(int(extra), MAX_EXTRA))
    table = _table()
    if table is None:
        raise RuntimeError("CACHE_TABLE is not set: no table to record the approval in")
    now = int(time.time())
    table.put_item(
        Item={
            "cache_key": row_key(phase, scope),
            "extra": extra,
            "approved_at": now,
            "approved_by": who[:100],
            "expires": now + APPROVAL_TTL_S,
            "ttl": now + 2 * APPROVAL_TTL_S,
        }
    )
    logger.info(
        "OBSERV batch.rounds_approved phase=%s scope=%s extra=%d by=%s",
        phase,
        scope,
        extra,
        who,
    )
    return extra
