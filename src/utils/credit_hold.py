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
  task definition with only its own env names, e.g. a ``contentrepository/`` key): the table is
  shared, so a stored entry is not trusted to name what to run (the phase-task, OCR and
  dispatcher roles are also denied writes to the gate's items, cloudformation/).
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
HOLDS_PREFIX = "credit#holds#"
HOLD_SHARDS = 16
CACHE_SECONDS = 300
FAILURE_SECONDS = 60
ALERT_HOURS = 6
# a claimed hold whose launch never confirmed (the Lambda died mid-launch) is retried after this
CLAIM_STALE_SECONDS = 3600
# holds replayed per hourly run: a large backlog drains over several hours instead of keeping the
# trigger's scheduled check (which runs the reconcile next) busy
RELEASE_PER_RUN = 25
# the env a held import may carry (its source key), and a held manual run (its declared count)
IMPORT_KEY = re.compile(r"contentrepository/[^\x00-\x1f]{1,900}")
COUNT = re.compile(r"[1-9][0-9]{0,6}")
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


def _shard_key(rid: str) -> str:
    """The hold's item: holds are spread over HOLD_SHARDS items so no item nears DynamoDB's
    400 KB limit (about 1 KB a hold, so thousands of holds), and listing them is HOLD_SHARDS
    reads, not a table scan."""
    return f"{HOLDS_PREFIX}{int(rid[:8], 16) % HOLD_SHARDS}"


def hold_keys() -> list:
    """Every hold item's key (for the IAM deny and the snapshot rules)."""
    return [f"{HOLDS_PREFIX}{n}" for n in range(HOLD_SHARDS)]


def _safe_env(env: Any, rules: Dict[str, "re.Pattern[str]"]) -> bool:
    """Only the env names ``rules`` allows for this task definition, each value matching its
    pattern (and never containing ``..``)."""
    if not isinstance(env, list):
        return False
    for e in env:
        if not isinstance(e, dict):
            return False
        value, rule = e.get("value"), rules.get(str(e.get("name")))
        if (
            rule is None
            or not isinstance(value, str)
            or ".." in value
            or not rule.fullmatch(value)
        ):
            return False
    return True


def valid_hold(req: Any, env_rules: Dict[str, Dict[str, "re.Pattern[str]"]]) -> bool:
    """A stored hold may be replayed only if it is a launch the gate could have held:
    ``env_rules`` maps each holdable task definition to the env names (and value patterns) it
    may carry; the book is a safe slug (not ``.``/``..``/``unknown``, which the launcher refuses
    and would only fail every hour); the source is short."""
    if not isinstance(req, dict) or req.get("task_def") not in env_rules:
        return False
    book, source = req.get("book", ""), req.get("source", "")
    return (
        isinstance(book, str)
        and bool(_BOOK.fullmatch(book))
        and book not in (".", "..", "unknown")
        and isinstance(source, str)
        and len(source) <= 64
        and _safe_env(req.get("extra_env") or [], env_rules[req["task_def"]])
    )


def hold_request(
    table: Any,
    task_def: str,
    source: str,
    book: str,
    extra_env: Optional[list],
) -> str:
    """Keep a launch that has no queue of its own (a Phase 0 import, an unscoped phase) until
    credit is back, as an entry of its shard's ``holds`` map. Returns the hold id;
    ``release_held`` replays it."""
    rid = hold_id(task_def, book, extra_env)
    request = json.dumps(
        {
            "task_def": task_def,
            "source": source[:64],
            "book": book,
            "extra_env": extra_env or [],
        }
    )
    key = {"cache_key": _shard_key(rid)}
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


def held_requests(
    table: Any, env_rules: Dict[str, Dict[str, "re.Pattern[str]"]]
) -> Dict[str, dict]:
    """Every replayable held launch: {hold id: request}. An entry that cannot be read or is not
    a launch this gate could have held is reported loudly and left in place for the operator.
    """
    out: Dict[str, dict] = {}
    for key in hold_keys():
        item = table.get_item(Key={"cache_key": key}).get("Item") or {}
        for rid, raw in (item.get("holds") or {}).items():
            try:
                req = json.loads(raw)
            except (TypeError, ValueError):
                req = None
            if isinstance(req, dict) and valid_hold(req, env_rules):
                out[str(rid)] = req
                continue
            logger.error(
                "OBSERV credit.hold_invalid id=%s status=error - a held launch is unreadable or "
                "not one the credit gate writes, so it is NOT replayed: inspect holds.%s in item "
                "%s of the api-cache table, re-trigger the launch by hand, then remove the entry",
                str(rid)[:64],
                str(rid)[:64],
                key,
            )
    return out


def _claim(table: Any, rid: str, now: float) -> bool:
    """Mark a hold as being launched (``claims.<id>`` = now), only if it still exists and is not
    claimed by a live reconcile. A claim older than CLAIM_STALE_SECONDS (its Lambda died) is
    taken over, so a crash never loses the hold."""
    try:
        table.update_item(
            Key={"cache_key": _shard_key(rid)},
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
    """Launched: the hold is done (removed). Not launched: only the claim goes, the hold stays.
    A failure here is logged: the stale claim is retried after CLAIM_STALE_SECONDS."""
    names = {"#c": "claims", "#id": rid}
    expr = "REMOVE #c.#id"
    if launched:
        names["#h"] = "holds"
        expr = "REMOVE #h.#id, #c.#id"
    try:
        table.update_item(
            Key={"cache_key": _shard_key(rid)},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
        )
    except Exception as e:  # noqa: BLE001 - see above
        logger.warning(
            "OBSERV credit.hold_finish_failed id=%s launched=%s err=%s status=warn - the claim "
            "stays; the hold is retried in %d min%s",
            rid[:64],
            launched,
            type(e).__name__,
            CLAIM_STALE_SECONDS // 60,
            (
                " (it may launch again: the import runs twice, nothing is lost)"
                if launched
                else ""
            ),
        )


def release_held(
    table: Any,
    launch: Callable[[dict], Any],
    held: Dict[str, dict],
    now: Optional[float] = None,
) -> Tuple[int, int]:
    """Replay each held launch through ``launch(request)``, which returns whether it launched.
    A hold is claimed first (two reconciles cannot both launch it) and removed only after its
    launch, so it is never lost: if the Lambda dies mid-launch the stale claim is retried an
    hour later (at worst the import runs twice; nothing is lost). One hold that fails
    does not stop the others. Returns (launched, failed)."""
    now = time.time() if now is None else now
    n = failed = tried = 0
    # start at a different hold each hour, so holds that keep failing at the front cannot keep
    # the rest from ever being tried
    items = list(held.items())
    start = int(now // 3600) % len(items) if items else 0
    for rid, req in items[start:] + items[:start]:
        if tried >= RELEASE_PER_RUN:
            logger.info(
                "OBSERV credit.release_capped tried=%d left=%d status=ok - the rest go next hour",
                tried,
                len(held) - tried,
            )
            break
        tried += 1
        if not _claim(table, rid, now):
            continue
        try:
            launched = bool(launch(req))
        except Exception as e:  # noqa: BLE001 - keep it; the next hour tries again
            failed += 1
            launched = False
            logger.warning(
                "OBSERV credit.release_one_failed id=%s family=%s err=%s status=warn - kept; "
                "retried next hour",
                rid[:64],
                str(req.get("task_def"))[:64],
                type(e).__name__,
            )
        _finish(table, rid, launched)
        n += launched
    return n, failed
