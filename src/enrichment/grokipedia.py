"""Shared, generic Grokipedia resolver — ONE place that turns a name into a Grokipedia
page URL, used by every entity that enriches from Grokipedia (people, places,
source_section, equipment, people_groups).

Grokipedia is client-side-rendered and has no API, so this is a best-effort HTML scrape of
its search page. We accept ONLY a clean slug (letters/digits/underscore/hyphen) and return
None otherwise — never a malformed URL built from a JS template fragment. Null-over-fake: a
miss returns None (and is negative-cached), never a guessed URL.
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
