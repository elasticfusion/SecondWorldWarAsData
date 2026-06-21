"""Equipment enrichment via Wikipedia.

Searches Wikipedia for equipment articles, extracts images + metadata.
Runs before OpenSERP so we can skip OpenSERP image search for items
that already have a Wikipedia photo.
"""

import json
import logging
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


def get_equipment_wikipedia_image(name: str) -> Optional[dict]:
    """Get cached Wikipedia image for equipment (found during enrich)."""
    return _equipment_wiki_images.get(name)


def search_equipment_wikipedia(name: str) -> Optional[dict]:
    """Search Wikipedia for an equipment article. Returns {extract, url, image, license} or None."""
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("wikipedia_equipment", name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return json.loads(cached)

    # Direct title lookup
    result = _lookup_equipment(name)
    if not result:
        # Try with common suffixes
        for suffix in [" tank", " aircraft", " gun", " rifle", " vehicle", " ship"]:
            result = _lookup_equipment(name + suffix)
            if result:
                break

    if result:
        cache_result("wikipedia_equipment", name, json.dumps(result))
        _equipment_wiki_images[name] = {"url": result.get("image", ""), "license": result.get("license", "unknown")}
    else:
        cache_result("wikipedia_equipment", name, None)
    return result


def _lookup_equipment(title: str) -> Optional[dict]:
    """Direct Wikipedia lookup for equipment page."""
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
            return None

        pages = resp.json().get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if page_id == "-1":
                continue
            extract = page_data.get("extract", "")
            if not extract:
                continue
            # Must be about military equipment (not a person/place)
            lower = extract.lower()
            if not any(kw in lower for kw in [
                "tank", "gun", "aircraft", "rifle", "weapon", "vehicle",
                "armored", "armoured", "artillery", "bomber", "fighter",
                "caliber", "calibre", "cannon", "machine gun", "mortar",
                "self-propelled", "howitzer", "anti-aircraft", "ship",
            ]):
                continue

            img_url = page_data.get("original", {}).get("source", "")
            license_info = None
            if img_url:
                filename = img_url.rsplit("/", 1)[-1]
                license_info = _fetch_license(filename)

            page_title = page_data.get("title", title)
            return {
                "extract": extract,
                "wikipedia_url": f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}",
                "image": img_url,
                "license": license_info or "unknown",
            }
    except Exception as e:
        logger.debug("Wikipedia equipment lookup failed for %s: %s", title, e)
    return None


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
    except Exception:
        pass
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

        # Skip if already enriched
        if data.get("wikipedia_url"):
            continue

        name = data.get("common_name", "")
        if not name or len(name) < 3:
            continue

        result = search_equipment_wikipedia(name)
        if result:
            data["wikipedia_url"] = result["wikipedia_url"]
            data["wikipedia_extract"] = result["extract"][:500]
            if result.get("image"):
                data.setdefault("images", []).insert(0, {
                    "url": result["image"],
                    "license": result["license"],
                    "source": "wikipedia",
                })
            data["enrichment_status"] = "enriched"
            f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            enriched += 1
            logger.info("  ✓ Wikipedia enriched equipment: %s → %s", name, result["wikipedia_url"])
        time.sleep(1)  # Rate limit

    logger.info("Equipment Wikipedia enrichment: %d enriched", enriched)
    return enriched
