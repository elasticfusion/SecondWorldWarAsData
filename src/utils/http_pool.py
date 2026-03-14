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
                allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"],
                **retry_kwargs,
            )
        except TypeError:
            retry_strategy = Retry(
                method_whitelist=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"],
                **retry_kwargs,
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
