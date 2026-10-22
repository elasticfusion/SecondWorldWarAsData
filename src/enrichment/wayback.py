"""Wayback Machine (archive.org) fallback for WAF-blocked / unreachable pages.

When a live fetch is blocked by a bot-protection layer (Incapsula/Imperva,
Cloudflare, Akamai, PerimeterX, ...) — which commonly returns an HTTP-200 *block
page* rather than an error status — we try the Internet Archive's Wayback Machine
for an archived snapshot of the same URL, so citation-grade content can still be
recovered. Where a snapshot is used, provenance (the archived URL + capture
timestamp) is returned so the record truthfully reflects that the content came
from the archive, not the live site.

POLITENESS (archive.org etiquette, mirrors the Nominatim client in this repo):
* An identifying, non-spoofed ``User-Agent`` naming the project (not a fake
  browser UA) so archive.org can attribute / contact.
* Requests spaced ``_MIN_INTERVAL`` apart (thread-safe), with jitter, so a
  corpus-wide pass never hammers the service.
* ``Retry-After`` honored on 429/503, bounded retries with backoff; on
  persistent throttling we give up gracefully (None) rather than retrying hard.
"""

import logging
import random
import threading
import time
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Identify ourselves to archive.org (etiquette) — mirrors nominatim_geocode._USER_AGENT.
_USER_AGENT = (
    "SecondWorldWarAsData/1.0 (+WWII historical data pipeline; archive.org fallback)"
)
_HEADERS = {"User-Agent": _USER_AGENT}

_AVAILABILITY_API = "https://archive.org/wayback/available"

# Be a good citizen: space archive.org calls out. Archive.org has no hard public
# rate limit, but courtesy spacing avoids pressure during a corpus-wide pass.
_MIN_INTERVAL = 2.0
_MAX_RETRIES = 3

_rate_lock = threading.Lock()
_last_call: dict = (
    {}
)  # host -> monotonic timestamp of last request (per-host politeness)

# Markers that identify a bot-protection / WAF block page served as HTTP-200 HTML
# (status-code checks alone miss these). Lower-cased substring match.
_BLOCK_MARKERS: Tuple[str, ...] = (
    "_incapsula_resource",  # Incapsula / Imperva
    "incident id",  # Incapsula block page wording
    "incapsula",
    "attention required",  # Cloudflare interstitial
    "cf-chl",  # Cloudflare challenge
    "cloudflare",  # (only with another marker; see is_block_page)
    "access denied",
    "request unsuccessful",  # Incapsula "Request unsuccessful."
    "px-captcha",  # PerimeterX
    "akamai",  # Akamai bot manager (with another marker)
    "please enable javascript and cookies",
    "verifying you are human",
    "ddos protection by",
)

# Strong markers that alone indicate a block (unambiguous).
_STRONG_BLOCK_MARKERS: Tuple[str, ...] = (
    "_incapsula_resource",
    "incident id",
    "request unsuccessful",
    "px-captcha",
    "cf-chl",
    "verifying you are human",
    "ddos protection by",
    "please enable javascript and cookies",
)


def is_block_page(html: Optional[str], status_code: Optional[int] = None) -> bool:
    """Heuristically detect a WAF / bot-protection block page.

    Handles the tricky Incapsula case where the block is served as HTTP-200 HTML.
    A hard 403/429/503 is treated as a block. For HTML, a single *strong* marker,
    or two or more *weak* markers in a short page, signals a block (two-marker rule
    avoids false positives on real articles that merely mention "Cloudflare").
    """
    if status_code in (403, 429, 503):
        return True
    if not html:
        return False
    low = html.lower()
    if any(m in low for m in _STRONG_BLOCK_MARKERS):
        return True
    # Weak markers: require >= 2 AND a short body (block pages are tiny).
    hits = sum(1 for m in _BLOCK_MARKERS if m in low)
    return hits >= 2 and len(html) < 4000


def _throttle(host: str) -> None:
    """Block so calls TO A GIVEN HOST are spaced >= _MIN_INTERVAL apart.

    Politeness is a per-host concern — the load lands on each distinct server — so the
    interval is tracked per hostname. In practice this module only ever talks to
    archive.org / web.archive.org, but keying on host keeps it correct-by-construction
    (and avoids throttling the availability-API host against the snapshot-content host
    unnecessarily)."""
    with _rate_lock:
        last = _last_call.get(host, 0.0)
        wait = _MIN_INTERVAL - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.3))  # jitter
        _last_call[host] = time.monotonic()


def _get_with_courtesy(url: str, timeout: int, **kwargs) -> Optional[requests.Response]:
    """GET with politeness: per-host throttle, identifying UA, Retry-After + bounded backoff."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc or "archive.org"
    for attempt in range(_MAX_RETRIES):
        _throttle(host)
        try:
            resp = requests.get(
                url, headers=_HEADERS, timeout=timeout, stream=True, **kwargs
            )
        except requests.RequestException as exc:
            logger.debug("wayback GET failed (%s): %s", exc, url)
            return None
        if resp.status_code in (429, 503):
            # Respect Retry-After if present, else exponential backoff; then retry.
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2.0 * (attempt + 1)
            except ValueError:
                delay = 2.0 * (attempt + 1)
            logger.debug(
                "wayback throttled (%d), backing off %.1fs (attempt %d/%d)",
                resp.status_code,
                delay,
                attempt + 1,
                _MAX_RETRIES,
            )
            time.sleep(min(delay, 30.0))
            continue
        # Byte-cap the archived body (stream=True) so a large snapshot can't OOM the task.
        _MAX = 10 * 1024 * 1024
        body = b""
        try:
            for chunk in resp.iter_content(8192):
                body += chunk
                if len(body) > _MAX:
                    logger.debug(
                        "wayback body exceeded %d bytes, truncating: %s", _MAX, url
                    )
                    break
            resp._content = (
                body  # noqa: SLF001 - materialize the capped body so .text works
            )
        except Exception as exc:  # noqa: BLE001 - read error -> treat as transient
            logger.debug("wayback body read failed (%s): %s", exc, url)
            return None
        return resp
    logger.debug("wayback gave up after %d throttled attempts: %s", _MAX_RETRIES, url)
    return None


def find_snapshot(url: str, timeout: int = 15) -> Optional[Tuple[str, str]]:
    """Return (archived_url, capture_timestamp) for the closest Wayback snapshot of
    ``url``, or None if nothing is archived. Timestamp is the Wayback 14-digit form."""
    if not url:
        return None
    resp = _get_with_courtesy(_AVAILABILITY_API, timeout=timeout, params={"url": url})
    if resp is None or resp.status_code != 200:
        _record_wayback_fetch(
            url,
            None,
            None,
            error=f"availability API status={getattr(resp, 'status_code', None)}",
        )
        return None
    try:
        data = resp.json()
    except ValueError:
        _record_wayback_fetch(
            url, None, None, error="availability API non-JSON response"
        )
        return None
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available") or not closest.get("url"):
        _record_wayback_fetch(url, None, None)
        return None
    archived_url, ts = str(closest["url"]), str(closest.get("timestamp", ""))
    _record_wayback_fetch(url, archived_url, ts)
    return archived_url, ts


def _record_wayback_fetch(url, archived_url, ts, error=None) -> None:
    """Record a textual fetch-provenance event for a Wayback lookup. Positive snapshot -> a
    hit enqueued for human review (review_status=pending); no snapshot -> a miss; an availability
    API transport/non-200/parse failure -> an error (API misbehavior stays walk-backable). Fail-safe.
    """
    try:
        from src.utils.fetch_provenance import (
            ERROR,
            HIT,
            MISS,
            TEXTUAL,
            record_fetch_event,
        )

        if error is not None:
            record_fetch_event(
                provider="wayback",
                endpoint=_AVAILABILITY_API,
                kind=TEXTUAL,
                request_params={"url": url},
                http_status=None,
                outcome=ERROR,
                result_summary="availability lookup failed",
                error=str(error),
            )
        elif archived_url:
            record_fetch_event(
                provider="wayback",
                endpoint=_AVAILABILITY_API,
                kind=TEXTUAL,
                request_params={"url": url},
                http_status=200,
                outcome=HIT,
                result_summary=f"snapshot {ts} -> {archived_url}",
            )
        else:
            record_fetch_event(
                provider="wayback",
                endpoint=_AVAILABILITY_API,
                kind=TEXTUAL,
                request_params={"url": url},
                http_status=200,
                outcome=MISS,
                result_summary="no snapshot",
            )
    except Exception:  # noqa: BLE001 - telemetry must never block the lookup
        pass


def fetch_archived(url: str, timeout: int = 20) -> Optional[Tuple[str, str, str]]:
    """Best-effort recovery of a WAF-blocked / dead page via the Wayback Machine.

    Returns (html, archived_url, capture_timestamp) when an archived snapshot exists
    and is itself not a block page; otherwise None. Provenance (archived_url +
    timestamp) lets the caller record that content came from the archive.
    """
    snap = find_snapshot(url, timeout=timeout)
    if not snap:
        logger.debug("no wayback snapshot for %s", url)
        return None
    archived_url, ts = snap
    # The archived_url comes from the availability-API response (normally web.archive.org, but vet
    # it): SSRF-recheck the resolved host + refuse redirects (a redirect off the archive host could
    # bounce to an internal address). The body is byte-capped in _get_with_courtesy.
    from urllib.parse import urlparse
    from src.utils.url_hygiene import resolved_host_is_safe

    arch_host = (urlparse(archived_url).hostname or "").lower()
    if resolved_host_is_safe(arch_host) is None:
        logger.warning("OBSERV wayback.archived_host_blocked host=%s", arch_host)
        return None
    resp = _get_with_courtesy(archived_url, timeout=timeout, allow_redirects=False)
    if resp is None or resp.status_code != 200 or not resp.text:
        return None
    # The crawler may itself have archived a block page — reject that too.
    if is_block_page(resp.text, resp.status_code):
        logger.debug("wayback snapshot is itself a block page: %s", archived_url)
        return None
    logger.info("recovered via Wayback (%s): %s", ts, url)
    return resp.text, archived_url, ts
