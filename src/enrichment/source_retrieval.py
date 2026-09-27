"""Source retrieval for resolved bibliography items.

Design intent (owner, 2026-09-27):
  - GRAB + DOWNLOAD legitimately-online sources, but DO NOT auto-process them —
    downloaded content is quarantined for HUMAN review before any downstream
    OCR/extraction/embedding.
  - The gate is NOT strict: download unless the source domain is on the existing
    ``config/domain_blacklist.yaml`` (license-rejected domains). Human review
    decides whether a retrieved source should actually be imported.
  - Legitimacy rationale: the pipeline downloads sources to AI-SUMMARIZE (Grok)
    and ATTRIBUTE them, not to republish verbatim — a transformative use — so a
    blacklist (not a strict allow-list) is the appropriate gate.

This module is the retrieval MECHANISM only. It is intentionally not wired to
run automatically in a phase; the human-disposition UI (tracked TODO) drives it.
"""

import logging
import random
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

from src.utils.http_pool import get_session

logger = logging.getLogger(__name__)

DEFAULT_BLACKLIST = Path("config/domain_blacklist.yaml")
# Quarantine holding area — deliberately NOT under content/ or contentrepository/
# so Phase 1 discovery can never auto-ingest a retrieved-but-unreviewed source.
QUARANTINE_PREFIX = "bibliography/retrieved/pending_review"

# Present as an ordinary browser client (blend in) — paired with courteous,
# human-paced rate limiting below. We identify as a normal browser, but we do
# NOT hammer: one request per domain every few seconds, honoring Retry-After.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/pdf,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class _DomainPacer:
    """Thread-safe per-domain rate limiter for courteous, human-paced downloads.

    Enforces a minimum interval between requests to the same host, with a small
    random jitter so the cadence isn't robotically uniform. This is politeness,
    not evasion — it keeps us from overloading archive servers.
    """

    def __init__(self, min_interval: float = 4.0, jitter: float = 1.5):
        self._min = min_interval
        self._jitter = jitter
        self._last: Dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        """Block until it is polite to hit ``host`` again."""
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            gap = self._min + random.uniform(0, self._jitter)
            sleep_for = last + gap - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last[host] = time.monotonic()


# Module-level pacer shared across retrievals (per-domain state).
_PACER = _DomainPacer()


def load_blacklist(path: Path = DEFAULT_BLACKLIST) -> List[str]:
    """Load blacklisted domains (license-rejected) from the shared config."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Could not load blacklist %s: %s", path, e)
        return []
    return [str(d).strip().lower() for d in (data.get("blacklist") or []) if d]


def is_blacklisted(url: str, blacklist: List[str]) -> bool:
    """True if the URL's host matches (or is a subdomain of) a blacklisted domain."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    for entry in blacklist:
        dom = entry.split("/")[0]  # entries may include a path fragment
        if host == dom or host.endswith("." + dom):
            return True
    return False


def retrieve_source(
    entry: Dict[str, Any],
    dest_dir: Path,
    blacklist: Optional[List[str]] = None,
    session: Any = None,
    max_bytes: int = 200 * 1024 * 1024,
) -> Dict[str, Any]:
    """Download a resolved entry's content into a quarantine dir for human review.

    Returns a result dict: {status, path?, url?, reason}. Never processes the
    content — only stores it and marks the entry ``retrieved_pending_review``.
    Non-blocking: any failure returns a status, does not raise.
    """
    if blacklist is None:
        blacklist = load_blacklist()

    urls = entry.get("resource_urls") or []
    if not urls:
        return {"status": "no_url", "reason": "entry has no resource_urls"}

    # Prefer a direct file (pdf) url if present.
    url = next((u for u in urls if u.lower().endswith(".pdf")), urls[0])

    if is_blacklisted(url, blacklist):
        entry["retrieval_status"] = "blacklisted"
        return {"status": "blacklisted", "url": url, "reason": "domain blacklisted"}

    sess = session or get_session()
    try:
        return _download_to_quarantine(entry, url, dest_dir, sess, max_bytes)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # network/IO — never block the pipeline
        return {"status": "error", "url": url, "reason": str(e)[:200]}


def _download_to_quarantine(
    entry: Dict[str, Any],
    url: str,
    dest_dir: Path,
    sess: Any,
    max_bytes: int,
) -> Dict[str, Any]:
    """Stream a URL to the quarantine dir; mark entry retrieved_pending_review.

    Polite + human-like: identifies as a browser, paces per-domain, and honors a
    429 Retry-After before giving up (does not hammer).
    """
    host = (urlparse(url).hostname or "").lower()
    _PACER.wait(host)  # courteous per-domain spacing
    resp = sess.get(
        url, timeout=60, stream=True, allow_redirects=True, headers=BROWSER_HEADERS
    )
    if resp.status_code == 429:
        # Rate-limited: wait the server-requested delay once, then retry.
        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
        logger.info("429 from %s — honoring Retry-After=%.0fs", host, retry_after)
        time.sleep(retry_after)
        resp = sess.get(
            url, timeout=60, stream=True, allow_redirects=True, headers=BROWSER_HEADERS
        )
    if resp.status_code != 200:
        return {"status": "error", "url": url, "reason": f"HTTP {resp.status_code}"}
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(url).path).name or "source"
    out = dest_dir / name
    written = 0
    with open(out, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                fh.close()
                out.unlink(missing_ok=True)
                return {
                    "status": "too_large",
                    "url": url,
                    "reason": f"exceeded {max_bytes} bytes",
                }
            fh.write(chunk)

    # Mark quarantined; explicitly NOT processed.
    entry["retrieval_status"] = "retrieved_pending_review"
    entry["retrieved_path"] = str(out)
    logger.info("Retrieved (pending review, not processed): %s -> %s", url, out)
    return {"status": "retrieved_pending_review", "url": url, "path": str(out)}


def _parse_retry_after(
    value: Optional[str], default: float = 10.0, cap: float = 120.0
) -> float:
    """Parse a Retry-After header (seconds form). Falls back to a sane default."""
    if not value:
        return default
    try:
        return min(float(value), cap)
    except (TypeError, ValueError):
        return default
