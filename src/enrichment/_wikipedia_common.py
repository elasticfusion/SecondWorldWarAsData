"""Shared Wikipedia/Wikimedia-Commons enrichment helpers.

equipment_wikipedia and groups_wikipedia are parallel entity-specific enrichers. Most of their
logic legitimately differs by entity kind (keyword whitelists, title-suffix expansion, cache keys),
but the Wikimedia-Commons image-license lookup is byte-identical between them (same endpoint, params,
headers, parse) apart from a log string. This module is the single source of truth for that call.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

WIKI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def fetch_commons_license(filename: str) -> Optional[str]:
    """Return the Wikimedia Commons ``LicenseShortName`` for ``File:<filename>``, or None.

    Best-effort (a license lookup must never break enrichment): any error is logged at debug and
    None is returned. Routed through recorded_fetch so the external call is provenance-recorded.
    """
    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "wikimedia-commons",
            "https://commons.wikimedia.org/w/api.php",
            kind=TEXTUAL,
            method="GET",
            params={
                "action": "query",
                "format": "json",
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "extmetadata",
            },
            headers=WIKI_HEADERS,
            timeout=10,
            result_summary_fn=lambda r: f"license status={r.status_code}",
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
