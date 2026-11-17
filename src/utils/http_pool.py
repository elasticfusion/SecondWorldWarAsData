"""HTTP connection pooling for improved performance."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Global sessions with connection pooling. Two variants share one configuration and differ only
# in whether the transport retries rate limits (429/503) itself (see get_session).
_session = None
_session_no_rate_retry = None

_RETRY_METHODS = ["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]


class _CallerHandlesRateLimitRetry(Retry):
    """Retry that never retries a rate limit (429/503); the caller applies its own policy.
    Removing them from status_forcelist is NOT enough: with respect_retry_after_header=True,
    urllib3 also retries every status in RETRY_AFTER_STATUS_CODES (413, 429, 503) that carries a
    Retry-After header, regardless of the forcelist (a 503 with Retry-After: 60 would still cost
    3 x 60 s)."""

    RETRY_AFTER_STATUS_CODES = frozenset({413})


def _build_session(retry_rate_limited: bool) -> requests.Session:
    session = requests.Session()
    # Retries are POLITE: exponential backoff AND honor the server's Retry-After header
    # (respect_retry_after_header).
    statuses = [429, 500, 502, 503, 504] if retry_rate_limited else [500, 502, 504]
    retry_kwargs = dict(
        total=3,
        backoff_factor=1,
        status_forcelist=statuses,
        # The rate-limit-aware variant must never sleep a server-supplied Retry-After itself
        # (urllib3 sleeps it uncapped for ANY retried status, e.g. a 502 with Retry-After: 120
        # would cost 360 s); its 5xx retries use the exponential backoff only.
        respect_retry_after_header=retry_rate_limited,
    )
    # allowed_methods was called method_whitelist in older urllib3
    retry_cls = Retry if retry_rate_limited else _CallerHandlesRateLimitRetry
    try:
        retry_strategy = retry_cls(allowed_methods=_RETRY_METHODS, **retry_kwargs)  # type: ignore[arg-type]
    except TypeError:
        retry_strategy = retry_cls(method_whitelist=_RETRY_METHODS, **retry_kwargs)  # type: ignore[call-arg, arg-type]

    adapter = HTTPAdapter(
        pool_connections=10,  # Number of connection pools
        pool_maxsize=20,  # Max connections per pool
        max_retries=retry_strategy,
        pool_block=False,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_session(retry_rate_limited: bool = True) -> requests.Session:
    """
    Get or create a global requests session with connection pooling.

    ``retry_rate_limited=False`` returns a session that still retries 500/502/504 but hands a
    429 or 503 straight back to the caller as a response. Use it when the caller has its own
    rate-limit policy (src.utils.wikimedia_client): the default session retries a 429/503 three
    times, sleeping the FULL Retry-After each time (Wikipedia sends 60 s, so one rate-limited
    lookup costs 180 s), then raises RetryError, so the caller cannot tell "rate limited" from
    "failed".

    Returns:
        Configured requests.Session with connection pooling
    """
    global _session, _session_no_rate_retry

    if retry_rate_limited:
        if _session is None:
            _session = _build_session(retry_rate_limited=True)
        return _session
    if _session_no_rate_retry is None:
        _session_no_rate_retry = _build_session(retry_rate_limited=False)
    return _session_no_rate_retry


def close_session():
    """Close the global sessions and cleanup connections."""
    global _session, _session_no_rate_retry
    for sess in (_session, _session_no_rate_retry):
        if sess is not None:
            sess.close()
    _session = None
    _session_no_rate_retry = None


# Full browser header profile. Testing (2026-10-02) showed many WAF-fronted public
# sources (London Gazette, CMOHS, TracesOfWar, Hall of Valor) return 200 to a plain
# HTTP client ONLY when it sends a complete browser-like header set — bare UA+Accept
# gets 403/challenge. These are the headers a real Chrome sends; use them for polite
# direct fetches of such sources (NOT for defeating a JS-interstitial challenge).
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def browser_headers(extra: dict = None) -> dict:
    """Return the full browser header profile (optionally merged with ``extra``).

    Use for polite direct fetches of WAF-fronted public sources that reject bare
    clients. Pass a descriptive ``From``/contact or override UA via ``extra`` if a
    source's policy prefers an identifying agent.
    """
    headers = dict(BROWSER_HEADERS)
    if extra:
        headers.update(extra)
    return headers
