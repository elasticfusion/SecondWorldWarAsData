"""Shared Grokipedia helpers — used by every entity that enriches from Grokipedia (people,
places, source_section, equipment, people_groups).

- resolve_grokipedia_url(name): turn a name into a Grokipedia /page/ URL. Grokipedia's search
  page is a client-side SPA, so this best-effort scrape accepts ONLY a clean slug and returns
  None otherwise (null-over-fake; negative-cached).
- fetch_grokipedia_rendered(url): capture the ARTICLE TEXT. Because the page is JS-rendered, a
  plain HTTP GET returns an empty shell; this uses headless Chromium (Playwright) to execute
  the JS and read the rendered text, degrading gracefully to None if the browser is absent.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}


def is_clean_slug(slug: str) -> bool:
    """A real Grokipedia slug is a bare token — no quotes, spaces, '+', or JS fragments."""
    return bool(slug) and re.fullmatch(r"[A-Za-z0-9_\-]+", slug) is not None


def resolve_grokipedia_url(name: str, timeout: int = 20) -> Optional[str]:
    """Resolve a name to a Grokipedia /page/ URL (clean-slug only). Cached (incl. negative).
    Returns None on a miss — null-over-fake."""
    if not name or len(name) < 3:
        return None
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("grokipedia_url", name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    url = _scrape_grokipedia_url(name, timeout)
    cache_result("grokipedia_url", name, url if url else None)
    return url


def _scrape_grokipedia_url(name: str, timeout: int) -> Optional[str]:
    try:
        resp = requests.get(
            f"https://grokipedia.com/search?q={name}",
            headers=_HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code != 200 or "/page/" not in resp.text:
            return None
        candidates = re.findall(r'data-slug="([^"]+)"', resp.text)
        candidates += re.findall(r"/page/([A-Za-z0-9_\-]+)", resp.text)
        for slug in candidates:
            if is_clean_slug(slug):
                return f"https://grokipedia.com/page/{slug}"
        return None
    except (requests.RequestException, ValueError) as e:
        logger.debug("grokipedia resolve failed for %r: %s", name, e)
        return None


# --- Headless-rendered article text --------------------------------------------------------

_MAX_RENDERED_CHARS = 8000  # bound the captured article text
# Chrome/nav chrome that leaks into inner_text("body") on Grokipedia; trimmed from the lead.
_CHROME_PREFIXES = ("⌘K", "Suggest edit", "Sign in", "Contents")


def fetch_grokipedia_rendered(url: str, timeout_ms: int = 30000) -> Optional[str]:
    """Render a Grokipedia page with headless Chromium (Playwright) and return the article
    text. Grokipedia is a client-side SPA, so a plain HTTP GET returns an empty shell — the
    article text only exists after JavaScript executes, which is what this does.

    GRACEFUL FALLBACK: returns None (never raises) if Playwright or the Chromium browser is
    not installed, or if the render fails/ times out — callers then keep their URL-only
    behavior. Bounded to _MAX_RENDERED_CHARS."""
    if not url:
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("playwright not installed — Grokipedia text skipped (URL-only).")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_UA)
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_timeout(1500)  # let late content settle
                # Prefer the article/main body; fall back to the whole page body.
                text = None
                for sel in ("article", "main", "body"):
                    try:
                        if page.query_selector(sel):
                            text = page.inner_text(sel)
                            break
                    except Exception:  # noqa: BLE001
                        continue
            finally:
                browser.close()
    except (
        Exception
    ) as e:  # noqa: BLE001 - render is best-effort; never block enrichment
        logger.warning("Grokipedia headless render failed for %s: %s", url, e)
        return None

    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    # Drop a leading run of UI chrome tokens if present.
    for prefix in _CHROME_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    return cleaned[:_MAX_RENDERED_CHARS] or None
