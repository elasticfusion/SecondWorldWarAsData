"""People groups (military units) enrichment via Wikipedia.

Searches Wikipedia for military unit articles, extracts images + metadata.
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

_groups_wiki_images: dict = {}
# H3: guarded — written during the threaded Wikipedia enrich + read later.
_groups_wiki_images_lock = threading.Lock()


def get_group_wikipedia_image(name: str) -> Optional[dict]:
    """Get cached Wikipedia image for a group."""
    with _groups_wiki_images_lock:
        return _groups_wiki_images.get(name)


def search_group_wikipedia(name: str) -> Optional[dict]:
    """Search Wikipedia for a military unit/group. Returns {extract, url, image, license} or None."""
    from src.utils.search_cache import cache_result, get_cached, TRANSIENT

    cached = get_cached("wikipedia_group", name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return json.loads(cached)

    transient = False
    result = _lookup_group(name)
    if result is TRANSIENT:
        transient = True
        result = None
    if not result:
        r = _lookup_group(f"{name} (World War II)")
        if r is TRANSIENT:
            transient = True
        elif r:
            result = r

    if result:
        cache_result("wikipedia_group", name, json.dumps(result))
        with _groups_wiki_images_lock:
            _groups_wiki_images[name] = {
                "url": result.get("image", ""),
                "license": result.get("license", "unknown"),
            }
    elif not transient:
        cache_result("wikipedia_group", name, None)  # genuine miss -> negative-cache
    return result


def _lookup_group(title: str):  # -> dict | None | search_cache.TRANSIENT sentinel
    """Direct Wikipedia lookup for a military unit page."""
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
            result = _extract_group_page(page_data, title)
            if result is not None:
                return result
    except Exception as e:
        logger.debug("Wikipedia group lookup failed for %s: %s", title, e)
        from src.utils.search_cache import TRANSIENT

        return TRANSIENT  # network/transient failure -> don't negative-cache
    return None


# Keywords that mark a Wikipedia extract as a military UNIT/organization.
_GROUP_KEYWORDS = (
    "division",
    "regiment",
    "brigade",
    "corps",
    "army",
    "battalion",
    "infantry",
    "armored",
    "armoured",
    "airborne",
    "panzer",
    "military",
    "forces",
    "command",
    "group",
)


def _extract_group_page(page_data: dict, title: str) -> Optional[dict]:
    """Build the group result dict from one Wikipedia page, or None if it has no usable extract or
    isn't about a military unit. Extracted from _lookup_group to keep it below the C gate.
    """
    extract = page_data.get("extract", "")
    if not extract:
        return None
    lower = extract.lower()
    if not any(kw in lower for kw in _GROUP_KEYWORDS):
        return None  # not a military unit/organization

    img_url = page_data.get("original", {}).get("source", "")
    license_info = None
    if img_url:
        license_info = _fetch_license(img_url.rsplit("/", 1)[-1])

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
        logger.debug("Group image license fetch failed: %s", e)
    return None


def enrich_group_from_wikipedia(group: dict) -> bool:
    """Phase-2 in-place Wikipedia enrichment for a single group record.

    Reuses search_group_wikipedia (the single source of the Wikipedia HTTP code — we do
    NOT duplicate it) to set wikipedia_url + wikipedia_extract + images on the group
    record. wikipedia_checked_at is stamped even on a MISS so we never re-fetch, and the
    whole step is GATED on wikipedia_checked_at (idempotent across runs). Performs NO
    file I/O — the caller (extractor) owns the native save path.

    Returns True if a Wikipedia hit was applied, else False.
    """
    from datetime import date

    # Gate: already checked (hit or miss) -> never re-fetch.
    if group.get("wikipedia_checked_at"):
        return False

    group["wikipedia_checked_at"] = date.today().isoformat()

    name = group.get("group_name", "")
    if not name or len(name) < 3:
        return False

    # Grokipedia (independent of the Wikipedia outcome; runs within the same gate).
    changed = False
    try:
        from src.enrichment.grokipedia import resolve_grokipedia_url

        grok_url = resolve_grokipedia_url(name)
        if grok_url:
            group["grokipedia_url"] = grok_url
            changed = True
    except Exception as e:  # noqa: BLE001 - best-effort, never block
        logger.debug("Grokipedia lookup failed for group %s: %s", name, e)

    result = search_group_wikipedia(name)
    if not result:
        logger.debug("No Wikipedia extract for group %s", name)
        return changed

    group["wikipedia_url"] = result["wikipedia_url"]
    group["wikipedia_extract"] = result["extract"][:500]
    if result.get("image"):
        group.setdefault("images", []).insert(
            0,
            {
                "url": result["image"],
                "license": result.get("license", "unknown"),
                "source": "wikipedia",
            },
        )
    logger.debug("Wikipedia enriched group %s → %s", name, result["wikipedia_url"])
    return True


def _apply_group_wikipedia(rec, result):
    """Stamp wikipedia_checked_at always; fold in result fields when present."""
    from datetime import date

    rec["wikipedia_checked_at"] = date.today().isoformat()
    if result:
        rec["wikipedia_url"] = result["wikipedia_url"]
        rec["wikipedia_extract"] = result["extract"][:500]
        if result.get("image"):
            rec.setdefault("images", []).insert(
                0,
                {
                    "url": result["image"],
                    "license": result["license"],
                    "source": "wikipedia",
                },
            )
        rec["enrichment_status"] = "enriched"
    return rec


def _enrich_one_group_file(f: Path) -> tuple[bool, bool]:
    """Process one group file. Returns (did_lookup, enriched).

    `did_lookup` is True only when the file passed all gates and a Wikipedia lookup ran
    (the point at which the original code applies its 1s rate-limit sleep). `enriched` is
    True iff a result was found. Preserves skip conditions (parse-fail, already-checked,
    missing/short name) and always stamps via update_enriched (negative-cache) on lookup.
    """
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return False, False

    if data.get("wikipedia_url") or data.get("wikipedia_checked_at"):
        return False, False

    name = data.get("group_name", "")
    if not name or len(name) < 3:
        return False, False

    result = search_group_wikipedia(name)

    def _apply(rec, _result=result):
        return _apply_group_wikipedia(rec, _result)

    from src.enrichment.enrich_write import update_enriched

    update_enriched(f, "people_groups", _apply)
    if result:
        logger.info(
            "  ✓ Wikipedia enriched group: %s → %s", name, result["wikipedia_url"]
        )
        return True, True
    return True, False


def enrich_all_groups_wikipedia(
    groups_dir: Path, max_items: Optional[int] = None
) -> int:
    """Enrich people_groups entities with Wikipedia data. Returns count enriched."""
    import time

    enriched = 0
    for f in sorted(groups_dir.glob("*.json")):
        if f.name == "index.json":
            continue
        if max_items and enriched >= max_items:
            break
        did_lookup, was_enriched = _enrich_one_group_file(f)
        if was_enriched:
            enriched += 1
        if did_lookup:
            time.sleep(1)

    logger.info("Groups Wikipedia enrichment: %d enriched", enriched)
    return enriched
