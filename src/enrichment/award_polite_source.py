"""Shared base for award-citation sources that enforces POLITE access for every
adapter — politeness is a rule for all sites, not a per-adapter afterthought.

A ``PoliteSource`` subclass gets, for free and uniformly:
  * a per-HOST rate limiter honoring the registry's ``crawl_delay`` (process-wide,
    thread-safe), so concurrent enrichment can't hammer a host;
  * an on-disk response cache (fetch each URL at most once per run/corpus);
  * the shared full browser header profile (http_pool.browser_headers);
  * primary-source PAGE PRESERVATION to the storage backend (local/S3);
  * fail-safe fetching (returns None on error; never raises to the caller).

Subclasses implement only parsing (``lookup``) and call ``self.polite_get(url)``.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Process-wide, per-host rate-limit state (shared across all PoliteSource instances).
_host_locks: Dict[str, threading.Lock] = {}
_host_last: Dict[str, float] = {}
_registry_lock = threading.Lock()

# Default politeness floor if a source declares no crawl_delay.
_DEFAULT_CRAWL_DELAY = 2.0


def _host_lock(host: str) -> threading.Lock:
    with _registry_lock:
        if host not in _host_locks:
            _host_locks[host] = threading.Lock()
        return _host_locks[host]


def _throttle_host(host: str, min_interval: float) -> None:
    """Block until at least ``min_interval`` seconds have passed since the last
    request to ``host`` (atomic check-then-sleep-then-update per host)."""
    lock = _host_lock(host)
    with lock:
        last = _host_last.get(host, 0.0)
        wait = min_interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.monotonic()


_CAP_MAX_BYTES = 10 * 1024 * 1024  # 10MB cap on a fetched (untrusted) source page


def _cap_body(resp, name: str, url: str) -> bool:
    """Materialize a BYTE-CAPPED body onto a streamed response so .text is bounded (an untrusted
    award source must not OOM the task with a multi-GB response). 10MB is ample for source HTML.
    """
    body = b""
    try:
        for chunk in resp.iter_content(8192):
            body += chunk
            if len(body) > _CAP_MAX_BYTES:
                logger.warning(
                    "%s body exceeded %d bytes, truncating [url=%s]",
                    name,
                    _CAP_MAX_BYTES,
                    url,
                )
                break
    except Exception as exc:  # noqa: BLE001 - mid-stream transport error -> transient
        logger.warning("%s body read failed [url=%s]: %s", name, url, exc)
        return False
    resp._content = body  # noqa: SLF001 - intentional: cap the body
    return True


class PoliteSource:
    """Base class: enforces rate-limit + cache + headers + preservation."""

    #: human-readable source name (stored in provenance); override in subclass.
    name: str = "polite source"

    def __init__(
        self,
        cache_dir: Path,
        *,
        source_id: str,
        crawl_delay: float = _DEFAULT_CRAWL_DELAY,
        session: Any = None,
        storage: Any = None,
    ):
        self._cache = Path(cache_dir)
        self._cache.mkdir(parents=True, exist_ok=True)
        self._source_id = source_id
        self._crawl_delay = max(float(crawl_delay or 0.0), 0.0)
        if session is None:
            from src.utils.http_pool import get_session

            session = get_session()
        self._session = session
        self._storage = storage
        self.last_error: Optional[str] = None  # URL-tagged last fetch error, if any

    def _ssrf_ok(self, url: str):
        """SSRF-validate an untrusted source url via the shared url_hygiene.vet_url_for_fetch
        chokepoint. Sets self.last_error on a block. Returns the VETTED IPs (for connection
        pinning) if safe, else None."""
        from src.utils.url_hygiene import vet_url_for_fetch

        vet = vet_url_for_fetch(url, scope="award")
        if vet is None:
            self.last_error = f"ssrf-blocked [url={url}]"
            return None
        return vet.vetted

    def _pinned_fetch(self, url, resolve_host, vetted, extra_headers):
        """Pinned GET to a vetted IP (closes DNS-rebind TOCTOU). _pinned_get records its own
        fetch-provenance event internally, so no manual record here (avoids double rows).
        """
        from src.utils.url_hygiene import _pinned_get
        from src.utils.http_pool import browser_headers

        return _pinned_get(
            url,
            resolve_host,
            vetted,
            timeout=30,
            headers=browser_headers(extra_headers),
        )

    def polite_get(
        self, url: str, *, extra_headers: Optional[dict] = None
    ) -> Optional[str]:
        """Rate-limited, cached, full-header GET that preserves the fetched page.

        Returns the response text, or None on cache-miss failure / non-200.
        """
        self.last_error = None
        key = self._cache / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.html"
        if key.exists():
            return key.read_text(encoding="utf-8", errors="replace")

        host = urlparse(url).netloc or self._source_id

        # The source url is UNTRUSTED (award-citation / search-derived). SSRF-gate AND capture the
        # vetted IPs so the fetch PINS to them — closing the DNS-rebind TOCTOU that a host-only
        # recorded_fetch left open (a short-TTL name could pass the resolve check then connect to an
        # internal/metadata address). Matches source_retrieval._download_to_quarantine.
        vetted = self._ssrf_ok(url)
        if vetted is None:
            return None
        resolve_host = (urlparse(url).hostname or "").lower()

        _throttle_host(host, self._crawl_delay)

        try:
            resp = self._pinned_fetch(url, resolve_host, vetted, extra_headers)
        except Exception as e:  # noqa: BLE001 - transient; caller tries next source
            msg = f"{type(e).__name__}: {e} [url={url}]"
            logger.warning("%s fetch failed %s", self.name, msg)
            self.last_error = msg
            return None

        if resp.status_code != 200:
            msg = f"HTTP {resp.status_code} [url={url}]"
            logger.warning("%s %s", self.name, msg)
            self.last_error = msg
            return None
        return self._handle_response(resp, url, key)

    def _handle_response(self, resp, url, key) -> Optional[str]:
        """Byte-cap the body, cache it, and preserve the page. Returns the text or None."""
        # Byte-cap the body (untrusted award source — a multi-GB response must not OOM the task).
        if not _cap_body(resp, self.name, url):
            self.last_error = f"body read failed [url={url}]"
            return None
        text = resp.text
        try:
            key.write_text(text, encoding="utf-8")
        except Exception as e:  # noqa: BLE001 - cache best-effort
            logger.debug("cache write skipped for %s: %s", url, e)

        # Preserve the fetched page as a retained primary-source record.
        if self._storage is not None:
            try:
                from src.enrichment.award_source_pages import preserve_page

                preserve_page(
                    self._storage,
                    self._source_id,
                    url,
                    text.encode("utf-8"),
                    resp.headers.get("Content-Type", "text/html"),
                )
            except Exception as e:  # noqa: BLE001 - preservation best-effort
                logger.warning("page preservation skipped: %s", e)

        return text

    # Subclasses must implement the AwardCitationSource protocol.
    def lookup(
        self, person_name: str, award_hint: str = ""
    ) -> List[Any]:  # pragma: no cover
        raise NotImplementedError
