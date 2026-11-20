"""The ONE prefix scan of the shared DynamoDB cache table (task 1.2, 2026-11-18).

The api-cache table holds queue rows (``pending#``), leases (``nat#lease#``), locks, batch jobs and
~33k cached answers (26 MB in Nov 2026). A DynamoDB Scan returns at most 1 MB per call, and
``Limit`` caps the items READ before the filter: a single unpaged call sees a few percent of the
table, so a queued book, a live NAT lease or a pending batch job beyond the first page was simply
not there. Every prefix lookup goes through ``scan_prefix``, which follows ``LastEvaluatedKey`` to
the end.

Cost: a full scan reads the whole table (~3,200 read units at 26 MB, about $0.001 on-demand). If
the table keeps growing, the next step is a small dedicated table or a sparse index for the
coordination rows; callers do not change.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, Optional, Sequence, Union

logger = logging.getLogger(__name__)


def scan_prefix(
    table: Any,
    prefix: Union[str, Sequence[str]],
    *,
    projection: Optional[str] = None,
    names: Optional[Dict[str, str]] = None,
    extra_filter: str = "",
    values: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield every item whose ``cache_key`` starts with ``prefix``, across all scan pages.
    ``prefix`` may be several prefixes: ONE scan matches any of them (each scan reads the whole
    table, so a caller that needs several prefixes pays once, not once per prefix).

    ``projection`` / ``names`` map to ProjectionExpression / ExpressionAttributeNames;
    ``extra_filter`` is ANDed to the prefix condition, with its placeholders in ``values``. It is
    expression TEXT: constants only, never caller data (that goes in ``values``).
    Errors propagate: a caller that must fail safe (keep NAT up on doubt) decides how.
    """
    prefixes = [prefix] if isinstance(prefix, str) else list(prefix)
    kwargs = _filter(prefixes, extra_filter, values or {})
    if projection:
        kwargs["ProjectionExpression"] = projection
    if names:
        kwargs["ExpressionAttributeNames"] = names
    pages = 0
    while True:
        resp = table.scan(**kwargs)
        pages += 1
        yield from resp.get("Items", [])
        start = resp.get("LastEvaluatedKey")
        if not start:
            break
        kwargs["ExclusiveStartKey"] = start
    logger.debug("scan_prefix prefix=%s pages=%d", ",".join(prefixes), pages)


def _check(prefixes: list, values: Dict[str, Any]) -> None:
    if not prefixes or not all(isinstance(p, str) and p for p in prefixes):
        raise ValueError("scan_prefix needs non-empty prefixes")
    if any(str(k).startswith(":scan_prefix") for k in values):
        raise ValueError(":scan_prefix* placeholders are reserved for the prefixes")


def _filter(
    prefixes: list, extra_filter: str, values: Dict[str, Any]
) -> Dict[str, Any]:
    """FilterExpression + values matching any of ``prefixes`` (ANDed with ``extra_filter``)."""
    _check(prefixes, values)
    holders = {
        (":scan_prefix" if i == 0 else f":scan_prefix{i}"): p
        for i, p in enumerate(prefixes)
    }
    match = " OR ".join(f"begins_with(cache_key, {h})" for h in holders)
    if len(holders) > 1:
        match = f"({match})"
    return {
        "FilterExpression": match + (f" AND ({extra_filter})" if extra_filter else ""),
        "ExpressionAttributeValues": {**values, **holders},
    }
