"""Equipment enrichment via Wikipedia.

Searches Wikipedia for equipment articles, extracts images + metadata.
Runs before OpenSERP so we can skip OpenSERP image search for items
that already have a Wikipedia photo.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Store Wikipedia images for equipment (checked by OpenSERP to skip duplicates)
_equipment_wiki_images: dict = {}
# H3: guarded — written during the threaded Wikipedia enrich + read later.
_equipment_wiki_images_lock = threading.Lock()


def get_equipment_wikipedia_image(name: str) -> Optional[dict]:
    """Get cached Wikipedia image for equipment (found during enrich)."""
    with _equipment_wiki_images_lock:
        return _equipment_wiki_images.get(name)


def search_equipment_wikipedia(name: str) -> Optional[dict]:
    """Search Wikipedia for an equipment article. Returns {extract, url, image, license} or None."""
    from src.utils.search_cache import cache_result, get_cached, TRANSIENT

    cached = get_cached("wikipedia_equipment", name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return json.loads(cached)

    # Direct title lookup, then common suffixes. Track whether any attempt hit a TRANSIENT failure
    # (non-200 / network) so a Wikipedia outage is not negative-cached as a genuine miss for 7d.
    transient = False
    result = _lookup_equipment(name)
    if result is TRANSIENT:
        transient = True
        result = None
    if not result:
        for suffix in [" tank", " aircraft", " gun", " rifle", " vehicle", " ship"]:
            r = _lookup_equipment(name + suffix)
            if r is TRANSIENT:
                transient = True
                continue
            if r:
                result = r
                break

    if result:
        cache_result("wikipedia_equipment", name, json.dumps(result))
        with _equipment_wiki_images_lock:
            _equipment_wiki_images[name] = {
                "url": result.get("image", ""),
                "license": result.get("license", "unknown"),
            }
    elif not transient:
        cache_result(
            "wikipedia_equipment", name, None
        )  # genuine miss -> negative-cache
    return result


def _lookup_equipment(title: str):  # -> dict | None | search_cache.TRANSIENT sentinel
    """Direct Wikipedia lookup for equipment page."""
    # URL-keyed dedup (not entity-keyed): skip if this article URL was fetched <90d ago.
    _canon = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    try:
        from src.utils import url_registry

        if not url_registry.should_fetch_content(_canon):
            return None
    except Exception:  # noqa: BLE001
        pass
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": title,
                "prop": "extracts|pageimages",
                "exintro": "True",
                "explaintext": "True",
                "redirects": "1",
                "piprop": "original",
            },
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            from src.utils.search_cache import TRANSIENT

            return TRANSIENT  # transient server error, not a genuine miss

        pages = resp.json().get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if page_id == "-1":
                continue
            result = _extract_equipment_page(page_data, title)
            if result is not None:
                return result
    except Exception as e:
        logger.debug("Wikipedia equipment lookup failed for %s: %s", title, e)
        from src.utils.search_cache import TRANSIENT

        return TRANSIENT  # network/transient failure -> don't negative-cache
    return None


# Keywords that mark a Wikipedia extract as military EQUIPMENT (not a person/place).
_EQUIPMENT_KEYWORDS = (
    "tank",
    "gun",
    "aircraft",
    "rifle",
    "weapon",
    "vehicle",
    "armored",
    "armoured",
    "artillery",
    "bomber",
    "fighter",
    "caliber",
    "calibre",
    "cannon",
    "machine gun",
    "mortar",
    "self-propelled",
    "howitzer",
    "anti-aircraft",
    "ship",
)


def _extract_equipment_page(page_data: dict, title: str) -> Optional[dict]:
    """Build the equipment result dict from one Wikipedia page, or None if it has no usable extract
    or isn't about equipment. Extracted from _lookup_equipment to keep it below the C gate.
    """
    extract = page_data.get("extract", "")
    if not extract:
        return None
    lower = extract.lower()
    if not any(kw in lower for kw in _EQUIPMENT_KEYWORDS):
        return None  # not military equipment (a person/place article)

    img_url = page_data.get("original", {}).get("source", "")
    license_info = None
    if img_url:
        # Wikipedia appends tracking params (?utm_source=…); strip query/fragment so the Commons
        # File: title is valid for license lookup.
        clean_url = img_url.split("?", 1)[0].split("#", 1)[0]
        license_info = _fetch_license(clean_url.rsplit("/", 1)[-1])

    page_title = page_data.get("title", title)
    _resolved = f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"
    try:
        from src.utils import url_registry as _reg

        _reg.finalize_searched(
            _resolved, relevant=True, context="wikipedia", source="wikipedia"
        )
    except Exception:  # noqa: BLE001
        pass
    return {
        "extract": extract,
        "wikipedia_url": _resolved,
        "image": img_url,
        "license": license_info or "unknown",
    }


def _fetch_license(filename: str) -> Optional[str]:
    """Fetch license from Wikimedia Commons."""
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "extmetadata",
            },
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for page_data in pages.values():
                ii = page_data.get("imageinfo", [])
                if ii:
                    meta = ii[0].get("extmetadata", {})
                    return meta.get("LicenseShortName", {}).get("value", "unknown")
    except Exception as e:  # noqa: BLE001 - license is best-effort, but log it
        logger.debug("Equipment image license fetch failed: %s", e)
    return None


def enrich_all_equipment_wikipedia(
    equipment_dir: Path, max_items: Optional[int] = None
) -> int:
    """Enrich equipment entities with Wikipedia data. Returns count enriched."""
    import time

    enriched = 0
    for f in sorted(equipment_dir.glob("*.json")):
        if f.name == "index.json":
            continue
        if max_items and enriched >= max_items:
            break
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Gate: skip if already enriched (has a wikipedia_url) OR checked recently. Avoids
        # redundant Wikipedia re-pulls on every Phase-3 run.
        if data.get("wikipedia_url") or data.get("wikipedia_checked_at"):
            continue

        name = data.get("common_name", "")
        if not name or len(name) < 3:
            continue

        result = search_equipment_wikipedia(name)

        def _apply(rec, _result=result):
            # Stamp a last-checked marker even on a miss, so we don't re-pull next run.
            from datetime import date

            rec["wikipedia_checked_at"] = date.today().isoformat()
            if _result:
                rec["wikipedia_url"] = _result["wikipedia_url"]
                rec["wikipedia_extract"] = _result["extract"][:500]
                if _result.get("image"):
                    rec.setdefault("images", []).insert(
                        0,
                        {
                            "url": _result["image"],
                            "license": _result["license"],
                            "source": "wikipedia",
                        },
                    )
                rec["enrichment_status"] = "enriched"
            return rec

        from src.enrichment.enrich_write import update_enriched

        update_enriched(f, "equipment", _apply)
        if result:
            enriched += 1
            logger.info(
                "  ✓ Wikipedia enriched equipment: %s → %s",
                name,
                result["wikipedia_url"],
            )
        time.sleep(1)  # Rate limit

    logger.info("Equipment Wikipedia enrichment: %d enriched", enriched)
    return enriched
