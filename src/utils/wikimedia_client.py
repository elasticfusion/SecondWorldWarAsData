"""Single choke point for Wikipedia / Wikimedia Commons API calls (people, groups, equipment,
places, articles, media).

Why one client: found in the XChannel-8 dev E2E (2026-11-06), every Wikipedia call site used the
default pooled session, which retries an HTTP 429 three times, sleeping the full ``Retry-After``
each time (Wikipedia sends 60 s), so ONE rate-limited lookup cost 180 s, then raised ``RetryError``.
Callers could not tell a rate limit from a real miss, and people were stamped ``not_found`` for the
90-day re-search window. Eleven call sites in eight modules each carried their own (divergent)
non-200 handling.

Policy (polite AND fast):

* A rate limit (429, or a 503 that carries ``Retry-After``) is honored, never fought. Its back-off
  is recorded per host (capped at :data:`MAX_BACKOFF_SECONDS`); until it passes, no request is
  sent to that host. Wikimedia's API guidelines require following instructions to delay further
  requests.
* A short wait (<= :data:`MAX_INLINE_WAIT_SECONDS`) is waited out in-process, by every thread that
  meets it, then the request proceeds; a longer one returns TRANSIENT (the caller defers the lookup
  to the next run).
* 403 (block) backs the host off for :data:`BLOCK_BACKOFF_SECONDS`; 5xx and transport errors are
  TRANSIENT.
* Every transient outcome is one loud ``OBSERV wikimedia.transient status=warn`` line.

Return contract: the ``requests.Response`` for any other status (200, 404, ...), or the
``search_cache.TRANSIENT`` sentinel.

Deferred subjects: a caller whose lookup came back TRANSIENT calls :func:`mark_transient`; the code
that would stamp the record (``not_found`` / ``wikipedia_checked_at``) asks
:func:`lookup_was_transient` and, if so, leaves it unstamped. Keys are ``(namespace, subject)`` so a
person, unit and place that share a name cannot clear each other's flag.

``tests/test_wikimedia_client.py`` enforces that no module calls a Wikimedia host except through
this client.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional, Set, Tuple
from urllib.parse import urlparse

import requests

from src.utils.search_cache import TRANSIENT

#: Providers routed through this client (fetch-provenance provider names).
WIKIMEDIA_PROVIDERS = ("wikipedia", "wikimedia-commons")

#: Namespaces for deferred subjects (one per stamping entity type).
PEOPLE = "people"
GROUPS = "wikipedia_group"
EQUIPMENT = "wikipedia_equipment"
PLACES = "places"

#: Longest back-off waited out in-process; anything longer defers the lookup.
MAX_INLINE_WAIT_SECONDS = 15
#: Assumed back-off when a rate limit carries no usable ``Retry-After`` (absent / HTTP-date).
DEFAULT_BACKOFF_SECONDS = 60
#: Ceiling on any stored back-off, so one hostile/buggy header cannot silence a host all run.
MAX_BACKOFF_SECONDS = 900
#: Back-off after a 403 (a Wikimedia block is host-wide, retrying at once only repeats it).
BLOCK_BACKOFF_SECONDS = 60
#: Bound on remembered deferred subjects (a long ECS task must not grow without limit).
MAX_DEFERRED = 10_000


_backoff_until: Dict[str, float] = {}  # host -> time.monotonic() deadline
_deferred: Set[Tuple[str, str]] = set()
_lock = threading.Lock()


def _retry_after_seconds(response: requests.Response) -> Optional[int]:
    """``Retry-After`` in whole seconds, or None when absent/unparseable (HTTP-date form)."""
    raw = (getattr(response, "headers", None) or {}).get("retry-after")
    if raw is None:
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def _transient(provider: str, subject: str, reason: str, **fields: Any) -> object:
    from src.utils.observe import observe_warn

    observe_warn(
        "wikimedia",
        "transient",
        provider=provider,
        subject=subject[:80] or None,
        reason=reason,
        note="treated as transient: not negative-cached, not marked not_found",
        **fields,
    )
    return TRANSIENT


def _backoff_remaining(host: str) -> float:
    with _lock:
        return _backoff_until.get(host, 0.0) - time.monotonic()


def _back_off(host: str, seconds: Optional[int]) -> float:
    """Record a back-off for ``host`` (capped); return the wait actually recorded."""
    wait = min(
        seconds if seconds is not None else DEFAULT_BACKOFF_SECONDS,
        MAX_BACKOFF_SECONDS,
    )
    with _lock:
        _backoff_until[host] = max(
            _backoff_until.get(host, 0.0), time.monotonic() + wait
        )
    return wait


def _wait_if_short(host: str) -> bool:
    """If ``host`` is backing off for <= MAX_INLINE_WAIT_SECONDS, sleep it out and return True.
    Return False if a longer back-off is active (caller defers). Not backing off -> True.
    """
    remaining = _backoff_remaining(host)
    if remaining <= 0:
        return True
    if remaining > MAX_INLINE_WAIT_SECONDS:
        return False
    time.sleep(remaining)
    return True


def reset_state() -> None:
    """Forget all recorded back-offs and deferred subjects (tests / a new process run)."""
    with _lock:
        _backoff_until.clear()
        _deferred.clear()


def mark_transient(namespace: str, subject: str, reason: str) -> None:
    """Record that the ``namespace`` lookup for ``subject`` failed transiently (loud)."""
    from src.utils.observe import observe_warn

    with _lock:
        if len(_deferred) >= MAX_DEFERRED:
            _deferred.clear()  # bounded; worst case a deferred record is stamped (prior behaviour)
        _deferred.add((namespace, subject[:200]))
    observe_warn(
        "wikimedia",
        "deferred",
        namespace=namespace,
        subject=subject[:80],
        reason=reason,
        note="record left unstamped; looked up again next run",
    )


def lookup_was_transient(namespace: str, subject: str, result: Any) -> bool:
    """True if the ``namespace`` lookup for ``subject`` found nothing (``result`` falsy) because of a
    transient failure. Always clears the flag, so a later lookup starts clean."""
    key = (namespace, subject[:200])
    with _lock:
        was = key in _deferred
        _deferred.discard(key)
    return was and not result


def _send(provider: str, endpoint: str, subject: str, request: Dict[str, Any]):
    """One GET through recorded_fetch (host-validated, provenance-recorded) on the session that
    leaves rate limits to us, unless the caller injected one. Transport errors (timeouts,
    connection errors, 5xx after retries) become TRANSIENT."""
    from src.utils.fetch_provenance import recorded_fetch
    from src.utils.http_pool import get_session

    # None, or the DEFAULT pooled session (callers like situational_geocode thread it through), would
    # put urllib3's own 429 retries back (3 x full Retry-After); use the rate-limit-aware session.
    # Any other injected session (a test double) is used as given.
    if request.get("session") in (None, get_session()):
        request = {**request, "session": get_session(retry_rate_limited=False)}
    try:
        return recorded_fetch(provider, endpoint, method="GET", **request)
    except requests.RequestException as e:
        return _transient(provider, subject, "fetch_error", error=type(e).__name__)


def _classify(resp, provider: str, subject: str, host: str):
    """Map a response to the return contract (TRANSIENT for rate limit / block / 5xx)."""
    if resp is TRANSIENT:
        return resp
    status = resp.status_code
    retry_after = _retry_after_seconds(resp) if status in (429, 503) else None
    if status == 429 or (status == 503 and retry_after is not None):
        # A bare 503 is an overloaded server, not an instruction to wait: http_error below.
        wait = _back_off(host, retry_after)
        return _transient(
            provider, subject, "rate_limited", http_status=status, backoff_s=wait
        )
    if status == 403:
        _back_off(host, BLOCK_BACKOFF_SECONDS)
        return _transient(provider, subject, "blocked", http_status=status)
    if status >= 500:
        return _transient(provider, subject, "http_error", http_status=status)
    return resp


def wikimedia_get(
    provider: str,
    endpoint: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 15,
    subject: str = "",
    kind: Optional[str] = None,
    result_summary_fn: Optional[Callable[[requests.Response], str]] = None,
    session: Optional[requests.Session] = None,
    **kwargs: Any,
):  # -> requests.Response | TRANSIENT
    """GET a Wikipedia/Wikimedia API endpoint under the shared rate-limit policy (module doc).

    ``subject`` names what is being looked up (person/unit/place) for the OBSERV line only.
    ``session`` is for dependency injection (tests); production callers omit it.
    Raises only what ``recorded_fetch`` raises for an unsafe host (``ValueError``).
    """
    from src.utils.fetch_provenance import TEXTUAL

    host = urlparse(endpoint).hostname or endpoint
    if not _wait_if_short(host):
        return _transient(
            provider,
            subject,
            "backoff_active",
            wait_s=int(_backoff_remaining(host)),
        )
    request = dict(
        kind=kind or TEXTUAL,
        params=params,
        headers=headers,
        timeout=timeout,
        result_summary_fn=result_summary_fn,
        session=session,
        **kwargs,
    )
    resp = _classify(
        _send(provider, endpoint, subject, request), provider, subject, host
    )
    remaining = _backoff_remaining(host) if resp is TRANSIENT else 0.0
    if 0 < remaining <= MAX_INLINE_WAIT_SECONDS:
        # A short rate-limit back-off was just recorded: wait it out, retry once.
        time.sleep(remaining)
        resp = _classify(
            _send(provider, endpoint, subject, request), provider, subject, host
        )
    return resp
