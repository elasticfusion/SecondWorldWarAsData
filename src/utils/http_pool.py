"""HTTP connection pooling for improved performance."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Global session with connection pooling
_session = None


def get_session() -> requests.Session:
    """
    Get or create a global requests session with connection pooling.

    Returns:
        Configured requests.Session with connection pooling
    """
    global _session

    if _session is None:
        _session = requests.Session()

        # Configure retry strategy
        retry_kwargs = dict(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        # allowed_methods was called method_whitelist in older urllib3
        try:
            retry_strategy = Retry(
                allowed_methods=[
                    "HEAD",
                    "GET",
                    "POST",
                    "PUT",
                    "DELETE",
                    "OPTIONS",
                    "TRACE",
                ],
                **retry_kwargs,  # type: ignore[arg-type]
            )
        except TypeError:
            retry_strategy = Retry(
                method_whitelist=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"],  # type: ignore[call-arg]
                **retry_kwargs,  # type: ignore[arg-type]
            )

        # Configure adapter with connection pooling
        adapter = HTTPAdapter(
            pool_connections=10,  # Number of connection pools
            pool_maxsize=20,  # Max connections per pool
            max_retries=retry_strategy,
            pool_block=False,
        )

        # Mount adapter for both http and https
        _session.mount("http://", adapter)
        _session.mount("https://", adapter)

    return _session


def close_session():
    """Close the global session and cleanup connections."""
    global _session
    if _session is not None:
        _session.close()
        _session = None


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
