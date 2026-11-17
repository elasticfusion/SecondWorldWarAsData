"""Shared Wikipedia/Wikimedia-Commons enrichment helpers.

equipment_wikipedia and groups_wikipedia are parallel entity-specific enrichers. Most of their
logic legitimately differs by entity kind (keyword whitelists, title-suffix expansion, cache keys),
but the Wikimedia-Commons image-license lookup is byte-identical between them (same endpoint, params,
headers, parse) apart from a log string. This module is the single source of truth for that call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

WIKI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def cached_variant_search(
    cache_ns: str,
    name: str,
    lookup: Callable[[str], Any],
    titles: Iterable[str],
    images: Dict[str, dict],
    images_lock: Any,
) -> Optional[dict]:
    """Shared search for the groups/equipment enrichers: search-cache read, then try each title in
    order (first real hit wins), then cache the outcome.

    ``lookup(title)`` returns a result dict, None (genuine miss) or ``search_cache.TRANSIENT``.
    A hit is cached and its image remembered; a genuine miss on every title is negative-cached; if
    any title failed TRANSIENTLY and nothing hit, nothing is cached and ``name`` is marked transient
    (src.utils.wikimedia_client) so the caller does not stamp the record as checked.
    """
    from src.utils.search_cache import TRANSIENT, cache_result, get_cached
    from src.utils.wikimedia_client import mark_transient

    cached = get_cached(cache_ns, name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return json.loads(cached)

    result, transient = None, False
    for title in titles:
        r = lookup(title)
        if r is TRANSIENT:
            transient = True
        elif r:
            result = r
            break

    if result:
        cache_result(cache_ns, name, json.dumps(result))
        with images_lock:
            images[name] = {
                "url": result.get("image", ""),
                "license": result.get("license", "unknown"),
            }
    elif transient:
        mark_transient(cache_ns, name, "lookup")
    else:
        cache_result(cache_ns, name, None)  # genuine miss -> negative-cache
    return result


def fetch_commons_license(filename: str) -> Optional[str]:
    """Return the Wikimedia Commons ``LicenseShortName`` for ``File:<filename>``, or None.

    Best-effort (a license lookup must never break enrichment): any error is logged at debug and
    None is returned. Routed through src.utils.wikimedia_client (shared rate-limit policy; provenance-recorded).
    """
    try:
        from src.utils.wikimedia_client import TRANSIENT, wikimedia_get

        resp = wikimedia_get(
            "wikimedia-commons",
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "extmetadata",
            },
            headers=WIKI_HEADERS,
            timeout=10,
            subject=filename,
            result_summary_fn=lambda r: f"license status={r.status_code}",
        )
        if resp is TRANSIENT:
            return (
                None  # license is best-effort; the client already logged the transient
            )
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for page_data in pages.values():
                ii = page_data.get("imageinfo", [])
                if ii:
                    meta = ii[0].get("extmetadata", {})
                    return meta.get("LicenseShortName", {}).get("value", "unknown")
    except Exception as e:  # noqa: BLE001 - license is best-effort, but log it
        logger.debug("Wikimedia Commons image license fetch failed: %s", e)
    return None
