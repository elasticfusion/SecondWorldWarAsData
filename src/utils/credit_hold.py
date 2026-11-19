"""Hold Grok-phase LAUNCHES while xAI credit is under the floor (task 1.1e, owner 2026-11-18).

Not ``src/utils/credit_gate`` (the batch spend reservation, M4.2): this module decides whether the
trigger Lambda should START a task at all.

The in-task preflight (``ecs_entrypoint._preflight_balance_check``, task 1.1c) stops a Grok phase
under the floor, but only after a Fargate task and the NAT have started just to read the balance;
while credit stays low every retry paid for that again. The trigger Lambda runs outside the VPC
and can read the balance without the NAT, so it asks first and does not launch a task that would
only stop. The in-task check stays the final word.

* The balance is read by the ``<env>-wwii-credit-check`` Lambda (lambda_handlers/credit_check.py),
  the only Lambda allowed the xAI management key, and cached as ``credit#balance`` for
  ``CACHE_SECONDS`` so a burst of launches costs one read; a failed read is remembered for
  ``FAILURE_SECONDS`` in-process so an xAI outage does not make every launch wait on it.
* Under the floor: no launch; the caller keeps the request (a Phase 2/3 queue row, or a credit
  hold here - never dropped) and one alert per ``ALERT_HOURS`` says how much credit there is.
* Balance unreadable here: launch anyway - the task's own preflight reads it again and stops
  (and alerts) if it really cannot; this gate only saves cost, it never guesses "low".
* A credit hold is replayed only if it is a launch this gate could have held (an allow-listed
  task definition and env names, a ``contentrepository/`` key): the table is writable by other
  roles, so a stored entry is not trusted to name what to run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from src.utils import grok_balance
from src.utils.observe import observe_alert, observe_warn_counted

logger = logging.getLogger(__name__)

BALANCE_KEY = "credit#balance"
ALERT_KEY = "credit#hold_alerted"
HOLDS_KEY = "credit#holds"
CACHE_SECONDS = 300
FAILURE_SECONDS = 60
ALERT_HOURS = 6
# a claimed hold whose launch never confirmed (the Lambda died mid-launch) is retried after this
CLAIM_STALE_SECONDS = 3600
HOLD_ENV_NAMES = frozenset({"CONVERT_KEY", "VIDEO_KEY"})
_HOLD_VALUE = re.compile(r"contentrepository/[^\x00-\x1f]{1,900}")
_BOOK = re.compile(r"[A-Za-z0-9._-]{0,128}")

_failure: Dict[str, Any] = {}  # in-process: the last failed read {"at", "reason"}


def credit_check_function() -> str:
    env = os.environ.get("ENV_NAME", "dev")
    return os.environ.get("CREDIT_CHECK_FN", f"{env}-wwii-credit-check")


def lambda_client() -> Any:
    """A Lambda client for the credit-check invoke: one try, a read timeout over the
    credit-check Lambda's own (a default client retries and doubles the wait)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "lambda",
        config=Config(connect_timeout=5, read_timeout=55, retries={"max_attempts": 1}),
    )


def _usable(usd: Any) -> Optional[float]:
    try:
        v = float(usd)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _cached(table: Any, now: float) -> Optional[float]:
    """The stored balance if 0 <= its age < CACHE_SECONDS (a future time is not fresh), else
    None."""
    try:
        item = table.get_item(Key={"cache_key": BALANCE_KEY}).get("Item") or {}
        data = json.loads(item.get("response") or "{}")
        if 0 <= now - float(data.get("at", 0)) < CACHE_SECONDS:
            return _usable(data.get("usd"))
    except Exception as e:  # noqa: BLE001 - a cache miss only costs one read
        logger.debug("credit cache unreadable: %s", type(e).__name__)
    return None


def _invoke_check(client: Any) -> dict:
    """Ask the credit-check Lambda; its answer ({"usd": x} or {"error": cause, ...})."""
    resp = client.invoke(
        FunctionName=credit_check_function(), InvocationType="RequestResponse"
    )
    if resp.get("FunctionError"):
        return {"error": "connectivity", "reason": "credit-check Lambda failed"}
    body = json.loads(resp["Payload"].read() or b"{}")
    return (
        body if isinstance(body, dict) else {"error": "config", "reason": "bad answer"}
    )


def balance(
    table: Any, client: Any, now: Optional[float] = None
) -> Tuple[Optional[float], str]:
    """(usd, reason): the live balance (cached), or (None, why) when it cannot be read."""
    now = time.time() if now is None else now
    usd = _cached(table, now)
    if usd is not None:
        return usd, ""
    if 0 <= now - _failure.get("at", -1e18) < FAILURE_SECONDS:
        return None, str(_failure.get("reason", ""))
    try:
        answer = _invoke_check(client)
    except Exception as e:  # noqa: BLE001 - unreadable: the task decides
        answer = {"reason": f"credit-check invoke: {type(e).__name__}"}
    usd = _usable(answer.get("usd"))
    if usd is None:
        reason = str(answer.get("reason") or answer.get("error") or "no balance")[:120]
        _failure.update(at=now, reason=reason)
        return None, reason
    _failure.clear()
    return usd, ""


def launch_allowed(
    table: Any,
    client: Any,
    config: Optional[dict],
    now: Optional[float] = None,
) -> Tuple[bool, Optional[float], float]:
    """(allowed, usd, floor) for a launch that will spend Grok credit. Not allowed only when the
    balance was READ and is under the floor; off (``api.grok.balance_check: false``) or
    unreadable is allowed (the task's preflight is the final word)."""
    floor, _, _ = grok_balance.thresholds(config)
    if not grok_balance.check_enabled(config):
        return True, None, floor
    usd, reason = balance(table, client, now)
    if usd is None:
        observe_warn_counted(
            "credit",
            "gate_unreadable",
            key=("trigger",),
            every=10,
            note=(
                "The trigger could not read the xAI balance, so it launches and lets the task's "
                "own preflight decide (that one alerts if the balance stays unreadable). Check the "
                "credit-check Lambda's logs if this repeats"
            ),
            reason=reason,
        )
        return True, None, floor
    logger.info(
        "OBSERV credit.gate usd=%.2f floor=%.2f allowed=%s status=ok",
        usd,
        floor,
        usd >= floor,
    )
    return usd >= floor, usd, floor


def alert_held(
    table: Any, usd: float, floor: float, what: str, now: Optional[float] = None
) -> bool:
    """One alert per ALERT_HOURS across Lambda invocations: a conditional put on
    ``credit#hold_alerted`` keyed on when it was last sent (a time in the future - a tampered or
    clock-skewed marker - does not silence it). Returns whether this call alerted."""
    now = time.time() if now is None else now
    try:
        table.put_item(
            Item={
                "cache_key": ALERT_KEY,
                "at": int(now),
                "ttl": int(now + ALERT_HOURS * 3600),
            },
            ConditionExpression=(
                "attribute_not_exists(cache_key) OR #at < :cutoff OR #at > :now"
            ),
            ExpressionAttributeNames={"#at": "at"},
            ExpressionAttributeValues={
                ":cutoff": int(now - ALERT_HOURS * 3600),
                ":now": int(now),
            },
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - already alerted in the window (or Dynamo down)
        if type(e).__name__ == "ConditionalCheckFailedException":
            logger.info(
                "OBSERV credit.hold usd=%.2f what=%s status=ok - already alerted",
                usd,
                what,
            )
            return False
        logger.warning("credit hold alert marker failed: %s", type(e).__name__)
    observe_alert(
        "credit",
        "launches_held",
        note=(
            f"xAI prepaid credit is ${usd:.2f}, under the ${floor:.2f} floor, so Grok phases are "
            "not being launched (Phase 1 parsing goes on). Nothing is dropped: queued books and "
            "imports start on their own within an hour of a top-up. Top up at "
            "https://console.x.ai (Billing)"
        ),
        dedup_key=str(int(now // (ALERT_HOURS * 3600))),
        balance_usd=f"{usd:.2f}",
        floor_usd=f"{floor:.2f}",
        held=what[:80],
    )
    return True


def hold_id(task_def: str, book: str, extra_env: Optional[list]) -> str:
    """The same launch held twice (a redelivered event) is ONE hold."""
    raw = json.dumps([task_def, book, extra_env or []], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _safe_env(env: Any) -> bool:
    """Only the import env names, each a ``contentrepository/`` key without ``..``."""
    if not isinstance(env, list):
        return False
    for e in env:
        value = e.get("value") if isinstance(e, dict) else None
        if (
            not isinstance(value, str)
            or e.get("name") not in HOLD_ENV_NAMES
            or ".." in value
            or not _HOLD_VALUE.fullmatch(value)
        ):
            return False
    return True


def valid_hold(req: Any, allowed_task_defs: Iterable[str]) -> bool:
    """A stored hold may be replayed only if it is a launch the gate could have held: an
    allow-listed task definition, a safe book name, a short source and only the import env.
    """
    if not isinstance(req, dict) or req.get("task_def") not in set(allowed_task_defs):
        return False
    book, source = req.get("book", ""), req.get("source", "")
    return (
        isinstance(book, str)
        and bool(_BOOK.fullmatch(book))
        and isinstance(source, str)
        and len(source) <= 64
        and _safe_env(req.get("extra_env") or [])
    )


def hold_request(
    table: Any,
    task_def: str,
    source: str,
    book: str,
    extra_env: Optional[list],
) -> str:
    """Keep a launch that has no queue of its own (a Phase 0 import, an unscoped phase) until
    credit is back, as one entry of the ``credit#holds`` map (one item: listing them is a single
    read, not a table scan). Returns the hold id; ``release_held`` replays it."""
    rid = hold_id(task_def, book, extra_env)
    request = json.dumps(
        {
            "task_def": task_def,
            "source": source[:64],
            "book": book,
            "extra_env": extra_env or [],
        }
    )
    key = {"cache_key": HOLDS_KEY}
    table.update_item(
        Key=key,
        UpdateExpression="SET #h = if_not_exists(#h, :e), #c = if_not_exists(#c, :e)",
        ExpressionAttributeNames={"#h": "holds", "#c": "claims"},
        ExpressionAttributeValues={":e": {}},
    )
    table.update_item(
        Key=key,
        UpdateExpression="SET #h.#id = :r",
        ExpressionAttributeNames={"#h": "holds", "#id": rid},
        ExpressionAttributeValues={":r": request},
    )
    return rid


def held_requests(table: Any, allowed_task_defs: Iterable[str]) -> Dict[str, dict]:
    """Every replayable held launch: {hold id: request}. An entry that cannot be read or is not
    a launch this gate could have held is reported loudly and left in place for the operator.
    """
    item = table.get_item(Key={"cache_key": HOLDS_KEY}).get("Item") or {}
    allowed = set(allowed_task_defs)
    out: Dict[str, dict] = {}
    for rid, raw in (item.get("holds") or {}).items():
        try:
            req = json.loads(raw)
        except (TypeError, ValueError):
            req = None
        if isinstance(req, dict) and valid_hold(req, allowed):
            out[str(rid)] = req
            continue
        logger.error(
            "OBSERV credit.hold_invalid id=%s status=error - a held launch is unreadable or not "
            "one the credit gate writes, so it is NOT replayed: inspect holds.%s in item %s of "
            "the api-cache table, re-trigger the import by hand, then remove the entry",
            str(rid)[:64],
            str(rid)[:64],
            HOLDS_KEY,
        )
    return out


def _claim(table: Any, rid: str, now: float) -> bool:
    """Mark a hold as being launched (``claims.<id>`` = now), only if it still exists and is not
    claimed by a live reconcile. A claim older than CLAIM_STALE_SECONDS (its Lambda died) is
    taken over, so a crash never loses the hold."""
    try:
        table.update_item(
            Key={"cache_key": HOLDS_KEY},
            UpdateExpression="SET #c.#id = :now",
            ConditionExpression=(
                "attribute_exists(#h.#id) AND "
                "(attribute_not_exists(#c.#id) OR #c.#id < :stale)"
            ),
            ExpressionAttributeNames={"#h": "holds", "#c": "claims", "#id": rid},
            ExpressionAttributeValues={
                ":now": int(now),
                ":stale": int(now - CLAIM_STALE_SECONDS),
            },
        )
        return True
    except Exception as e:  # noqa: BLE001 - claimed elsewhere, or retry next hour
        logger.info("credit hold %s not claimed: %s", rid[:64], type(e).__name__)
        return False


def _finish(table: Any, rid: str, launched: bool) -> None:
    """Launched: the hold is done (removed). Not launched: only the claim goes, the hold stays."""
    names = {"#c": "claims", "#id": rid}
    expr = "REMOVE #c.#id"
    if launched:
        names["#h"] = "holds"
        expr = "REMOVE #h.#id, #c.#id"
    table.update_item(
        Key={"cache_key": HOLDS_KEY},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
    )


def release_held(
    table: Any,
    launch: Callable[[dict], Any],
    held: Dict[str, dict],
    now: Optional[float] = None,
) -> int:
    """Replay each held launch through ``launch(request)``, which returns whether it launched.
    A hold is claimed first (two reconciles cannot both launch it) and removed only after its
    launch, so it is never lost: if the Lambda dies mid-launch the stale claim is retried an
    hour later (at worst a second launch, which the family lock serialises). Returns how many
    launched."""
    now = time.time() if now is None else now
    n = 0
    for rid, req in held.items():
        if not _claim(table, rid, now):
            continue
        try:
            launched = bool(launch(req))
        except Exception:
            _finish(table, rid, launched=False)
            raise
        _finish(table, rid, launched)
        n += launched
    return n
