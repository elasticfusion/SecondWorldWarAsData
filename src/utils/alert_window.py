"""At most one alert per time window across processes (Lambda invocations, tasks), through one
DynamoDB item per alert kind - the shared rule for repeating reminders (DRY: the credit-hold alert,
the held-book reminder).

``claim(table, key, seconds)`` is a conditional put on ``key`` keyed on when the alert was last
sent: it succeeds (True: send now) when there is no marker, the marker is older than the window,
the marker is in the FUTURE, or its time is missing or not a number (a tampered or clock-skewed
marker must not silence the alert). ``release`` gives a claim back when the alert was not sent.
A DynamoDB error other than the condition also returns True: a reminder is better twice than never.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def claim(table: Any, key: str, seconds: int, now: Optional[float] = None) -> bool:
    """True when this caller should send the alert for ``key`` now (see the module docstring)."""
    now = time.time() if now is None else now
    try:
        table.put_item(
            Item={"cache_key": key, "at": int(now), "ttl": int(now + seconds)},
            ConditionExpression=(
                "attribute_not_exists(cache_key) OR attribute_not_exists(#at) OR "
                "NOT attribute_type(#at, :num) OR #at < :cutoff OR #at > :now"
            ),
            ExpressionAttributeNames={"#at": "at"},
            ExpressionAttributeValues={
                ":cutoff": int(now - seconds),
                ":now": int(now),
                ":num": "N",
            },
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - sent in this window already (or DynamoDB down)
        if type(e).__name__ == "ConditionalCheckFailedException":
            return False
        logger.warning(
            "OBSERV alert_window.marker_failed key=%s err=%s status=warn - alerting anyway",
            key,
            type(e).__name__,
        )
    return True


def release(table: Any, key: str) -> None:
    """The alert claimed for ``key`` was not delivered: drop the marker so the next caller sends
    it. Never raises (a marker left behind only delays the alert by one window)."""
    try:
        table.delete_item(Key={"cache_key": key})
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV alert_window.release_failed key=%s err=%s status=warn - the alert waits "
            "one window",
            key,
            type(e).__name__,
        )
