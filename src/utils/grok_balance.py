"""The live xAI prepaid credit balance and the owner's thresholds (task 1.1c, owner 2026-11-18):

* under ``api.grok.credit_floor_usd`` ($50) when a Grok phase starts: stop before any Grok spend,
  alert, defer the book ``funding`` (it resumes on its own after a top-up);
* at or under ``api.grok.credit_alert_usd`` ($10) during a run: a loud alert, repeated every hour
  while it lasts; the run continues (if xAI then refuses for credit, HTTP 402, task 1.2e defers
  the book ``funding``);
* balance unreadable: stop, do not guess.

The balance is NOT ``/prepaid/balance``'s ``total``: that is a ledger, and xAI posts each month's
spending to it only on the 11th of the next month (2026-11-18: ledger $192.59, console $62.31).
``/postpaid/invoice/preview`` carries both the ledger credit (``prepaidCredits``, negative =
credit) and this month's use of it (``prepaidCreditsUsed``); credit minus use is what the console
shows. One GET per check, no Grok tokens.

Needs a Management API key (it can buy credit and manage keys): in ECS the wrapper loads Secrets
Manager ``<env>-wwii-pipeline/xai-management-key`` with ``set_management_key`` - kept in this
module, NOT ``os.environ``, so no phase subprocess inherits it; locally ``GROK_MANAGEMENT_API_KEY``
(``.env``). The team ID comes from the inference key (``GET /v1/api-key``), so it is never
configured by hand.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

API_KEY_URL = "https://api.x.ai/v1/api-key"
PREVIEW_URL = (
    "https://management-api.x.ai/v1/billing/teams/{team}/postpaid/invoice/preview"
)
MANAGEMENT_KEY_ENV = "GROK_MANAGEMENT_API_KEY"
FLOOR_USD = 50.0
ALERT_USD = 10.0
CHECK_MINUTES = 15
_TIMEOUT = (5, 15)  # connect, read
# a balance outside this many cents ($100k) is not this account's balance (a changed, broken or
# tampered answer): unreadable
_SANE_CENTS = 10**7
_TEAM_ID = re.compile(r"[A-Za-z0-9-]{1,64}")

# sha256(inference key) -> team id, cached for the process (the key itself is not kept)
_team: Dict[str, str] = {}
_management_key: Dict[str, str] = {}


def set_management_key(key: str) -> None:
    """Hold the management key for this process only (the ECS wrapper, from Secrets Manager)."""
    _management_key["key"] = key.strip()


def _key_of(management_key: str) -> str:
    return (
        management_key
        or _management_key.get("key", "")
        or os.environ.get(MANAGEMENT_KEY_ENV, "").strip()
    )


class BalanceUnreadable(Exception):
    """The live balance could not be read. ``cause`` is the queue cause to defer with: ``config``
    (no or rejected key, an answer that is not a balance: held for the operator) or
    ``connectivity`` (network, timeout, xAI 5xx/429: resumes on its own)."""

    def __init__(self, cause: str, reason: str):
        super().__init__(reason)
        self.cause = cause
        self.reason = reason


def check_enabled(config: Optional[dict]) -> bool:
    """``api.grok.balance_check`` (default on). An open-source deployment that does not use
    prepaid xAI credit, or has no management key, switches it off explicitly; only an explicit
    ``false`` turns it off (a typo keeps the check)."""
    grok = ((config or {}).get("api") or {}).get("grok") or {}
    return grok.get("balance_check", True) is not False


def thresholds(config: Optional[dict]) -> Tuple[float, float, int]:
    """(floor_usd, alert_usd, check_minutes) from ``api.grok`` in config.yaml, else the owner's
    defaults. A value that is not a positive number falls back to its default (never 0: a 0
    floor would let a run start with no credit)."""
    grok = ((config or {}).get("api") or {}).get("grok") or {}

    def pick(key: str, default: float) -> float:
        try:
            v = float(grok.get(key, default))
        except (TypeError, ValueError):
            return default
        return v if v > 0 else default

    floor, alert = pick("credit_floor_usd", FLOOR_USD), pick(
        "credit_alert_usd", ALERT_USD
    )
    if alert >= floor:
        logger.warning(
            "OBSERV grok.thresholds_invalid floor=%.2f alert=%.2f status=warn - "
            "api.grok.credit_alert_usd must be under credit_floor_usd; using $%.0f / $%.0f. "
            "Fix config.yaml",
            floor,
            alert,
            FLOOR_USD,
            ALERT_USD,
        )
        floor, alert = FLOOR_USD, ALERT_USD
    return floor, alert, int(pick("balance_check_minutes", CHECK_MINUTES))


def _get(session: Any, url: str, key: str, what: str) -> dict:
    """GET ``url`` with ``key``; the JSON body, or BalanceUnreadable with the right cause."""
    try:
        resp = session.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=_TIMEOUT,
            allow_redirects=False,  # a redirect is not the answer (and must not carry the key)
        )
    except Exception as e:  # noqa: BLE001 - any transport failure is an outage
        raise BalanceUnreadable("connectivity", f"{what}: {type(e).__name__}") from e
    code = getattr(resp, "status_code", 0)
    if code in (401, 403):
        raise BalanceUnreadable("config", f"{what}: HTTP {code} (key rejected)")
    if code == 429 or code >= 500:
        raise BalanceUnreadable("connectivity", f"{what}: HTTP {code}")
    if code != 200:
        raise BalanceUnreadable("config", f"{what}: HTTP {code}")
    try:
        body = resp.json()
    except ValueError as e:
        raise BalanceUnreadable("config", f"{what}: answer is not JSON") from e
    if not isinstance(body, dict):
        raise BalanceUnreadable("config", f"{what}: answer is not an object")
    return body


def team_id(api_key: str, session: Any) -> str:
    """The team of the inference key (cached per process)."""
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    if digest in _team:
        return _team[digest]
    tid = _get(session, API_KEY_URL, api_key, "team lookup").get("team_id")
    if not isinstance(tid, str) or not _TEAM_ID.fullmatch(tid):
        raise BalanceUnreadable("config", "team lookup: no usable team_id")
    _team[digest] = tid
    return tid


def _cents(obj: Any, field: str) -> int:
    """An xAI ``{"val": "<int cents>"}`` field as an int, or BalanceUnreadable."""
    holder = obj.get(field) if isinstance(obj, dict) else None
    val = holder.get("val") if isinstance(holder, dict) else None
    try:
        cents = int(str(val))
    except (TypeError, ValueError) as e:
        raise BalanceUnreadable("config", f"invoice preview: no {field}") from e
    if abs(cents) > _SANE_CENTS:
        raise BalanceUnreadable("config", f"invoice preview: {field} out of range")
    return cents


def balance_usd(
    management_key: str = "", api_key: str = "", session: Any = None
) -> float:
    """The live prepaid credit balance in USD (what the xAI console shows). Raises
    BalanceUnreadable; never guesses."""
    management_key = _key_of(management_key)
    api_key = api_key or os.environ.get("GROK_API_KEY", "")
    if not management_key or not api_key:
        missing = MANAGEMENT_KEY_ENV if not management_key else "GROK_API_KEY"
        raise BalanceUnreadable("config", f"{missing} is not set")
    if session is None:
        import requests

        session = requests
    team = team_id(api_key, session)
    body = _get(
        session, PREVIEW_URL.format(team=team), management_key, "invoice preview"
    )
    invoice = body.get("coreInvoice")
    credit = -_cents(invoice, "prepaidCredits")  # negative = credit bought
    used = abs(_cents(invoice, "prepaidCreditsUsed"))  # this month's use of it
    return round((credit - used) / 100.0, 2)


class LowBalanceMonitor:
    """During a run, read the balance every ``minutes`` and call ``alert(usd)`` while it is at or
    under ``alert_usd`` (the caller dedups per hour, so the alert repeats while it lasts). An
    unreadable balance mid-run is reported through ``unreadable(reason)`` and the run goes on: a
    real exhaustion still stops it (HTTP 402, task 1.2e). Never raises into the run."""

    def __init__(
        self,
        alert_usd: float,
        minutes: int,
        alert: Callable[[float], None],
        unreadable: Callable[[str], None],
        read: Callable[[], float] = balance_usd,
    ):
        self._alert_usd = alert_usd
        self._interval = max(60, int(minutes) * 60)
        self._alert = alert
        self._unreadable = unreadable
        self._read = read
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def check(self) -> Optional[float]:
        """One check; the balance read, or None when unreadable."""
        try:
            usd = self._read()
        except BalanceUnreadable as e:
            self._unreadable(e.reason)
            return None
        except Exception as e:  # noqa: BLE001 - a monitor bug must not stop the run
            self._unreadable(type(e).__name__)
            return None
        logger.info(
            "OBSERV grok.balance usd=%.2f alert_at=%.2f status=%s",
            usd,
            self._alert_usd,
            "warn" if usd <= self._alert_usd else "ok",
        )
        if (
            usd <= self._alert_usd and not self._stop.is_set()
        ):  # not after the phase ended
            self._alert(usd)
        return usd

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.check()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="grok-balance", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


def hour_bucket(now: Optional[float] = None) -> int:
    """The current hour, for an alert that repeats hourly while its condition lasts."""
    return int((time.time() if now is None else now) // 3600)
