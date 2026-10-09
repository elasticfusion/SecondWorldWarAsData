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
            resp = requests.get(url, headers=_HEADERS, timeout=timeout, **kwargs)
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
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available") or not closest.get("url"):
        return None
    return str(closest["url"]), str(closest.get("timestamp", ""))


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
    resp = _get_with_courtesy(archived_url, timeout=timeout, allow_redirects=True)
    if resp is None or resp.status_code != 200 or not resp.text:
        return None
    # The crawler may itself have archived a block page — reject that too.
    if is_block_page(resp.text, resp.status_code):
        logger.debug("wayback snapshot is itself a block page: %s", archived_url)
        return None
    logger.info("recovered via Wayback (%s): %s", ts, url)
    return resp.text, archived_url, ts
