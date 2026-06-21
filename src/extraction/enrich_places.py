"""Enrich places with hierarchy and historical names via Grok + Wikipedia fallback."""

import json
import logging
from pathlib import Path
from typing import Optional

import requests


def _today():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _should_re_search(data: dict) -> bool:
    """Check if a not_found entity should be re-searched based on age."""
    from datetime import datetime

    from src.utils.config import load_config

    days = load_config().get("enrichment", {}).get("re_search_after_days", 90)
    last_search = data.get("last_enrichment_search")
    if not last_search:
        return True
    try:
        return (
            datetime.now() - datetime.strptime(last_search, "%Y-%m-%d")
        ).days >= days
    except (ValueError, TypeError):
        return True


from src.grok_client import BatchModeCollecting, GrokClient
from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)

SKIP_FILES = frozenset(
    ["index.json", "duplicate_report.json", ".processed_events.json"]
)

_WIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

_WIKI_API = "https://en.wikipedia.org/w/api.php"

PROMPT = """Look up this WWII-era geographic location: {name}

Return JSON with:
- continent: Which continent (Europe, Asia, Africa, North America, South America, Oceania)
- country: Country name (use WWII-era country if different from modern, e.g. "Germany" not "West Germany")
- region: Administrative region/state/province (e.g. "Normandy", "Bavaria"). null if unknown
- historical_names: Array of {{"name": "...", "language": "English|German|French|Russian|Italian|Japanese|Polish|Other", "date_range": "1939-1945"}} for names used during WWII that differ from the current name. Empty array if name unchanged.

CRITICAL: Only include facts you are confident about. Use null for unknown fields.
Return ONLY valid JSON, no markdown."""

_CONTINENT_KEYWORDS = {
    "Europe": ["Europe", "European"],
    "Asia": ["Asia", "Asian"],
    "Africa": ["Africa", "African"],
    "North America": ["North America", "United States", "Canada", "Mexico"],
    "South America": ["South America"],
    "Oceania": ["Oceania", "Pacific", "Australia"],
}


def _fetch_place_wikipedia_full(name: str) -> Optional[dict]:
    """Fetch Wikipedia URL + image for a place."""
    try:
        resp = requests.get(
            _WIKI_API,
            params={
                "action": "query",
                "format": "json",
                "titles": name,
                "prop": "extracts|pageimages",
                "exintro": "True",
                "explaintext": "True",
                "redirects": "1",
                "piprop": "original",
            },
            headers=_WIKI_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        pages = resp.json().get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if page_id == "-1" or page_data.get("missing") is not None:
                continue
            page_title = page_data.get("title", name)
            img_url = page_data.get("original", {}).get("source", "")
            license_info = None
            if img_url:
                filename = img_url.rsplit("/", 1)[-1]
                license_info = _fetch_image_license(filename)
            return {
                "wikipedia_url": f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}",
                "image": img_url,
                "license": license_info or "unknown",
            }
    except Exception:
        pass
    return None


def _fetch_image_license(filename: str) -> Optional[str]:
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
            headers=_WIKI_HEADERS,
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


def _search_grokipedia_place(name: str) -> Optional[str]:
    """Search Grokipedia for a place. Returns page URL or None."""
    import re

    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("grokipedia_place", name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    try:
        resp = requests.get(
            f"https://grokipedia.com/search?q={name} World War",
            headers=_WIKI_HEADERS,
            timeout=15,
        )
        if resp.status_code == 200 and "/page/" in resp.text:
            # Find page links matching the place name
            page_links = re.findall(r'href="/page/([^"]+)"', resp.text)
            name_lower = name.lower().replace(" ", "")
            for link in page_links:
                link_lower = link.lower().replace("_", "")
                if name_lower in link_lower or link_lower in name_lower:
                    url = f"https://grokipedia.com/page/{link}"
                    cache_result("grokipedia_place", name, url)
                    return url
    except Exception:
        pass
    cache_result("grokipedia_place", name, None)
    return None


def _search_wikipedia(name: str, timeout: int = 15) -> Optional[dict]:
    """Query Wikipedia API for place info. Returns {country, continent, region} or None."""
    try:
        resp = requests.get(
            _WIKI_API,
            params={  # type: ignore[arg-type]
                "action": "query",
                "format": "json",
                "titles": name,
                "prop": "extracts|categories",
                "exintro": True,
                "explaintext": True,
                "cllimit": 20,
            },
            headers=_WIKI_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        pages = resp.json().get("query", {}).get("pages", {})
        page: dict = next(iter(pages.values()), {})
        if page.get("missing") is not None:
            return None
        return _parse_wiki_page(page)
    except (requests.RequestException, StopIteration):
        return None


def _parse_wiki_page(page: dict) -> dict:
    """Extract hierarchy from Wikipedia page extract and categories."""
    extract = page.get("extract", "")
    cats = [c.get("title", "") for c in page.get("categories", [])]
    cat_text = " ".join(cats)
    combined = f"{extract} {cat_text}"

    result: dict = {}

    # Detect continent
    for continent, keywords in _CONTINENT_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            result["continent"] = continent
            break

    # Extract country from categories like "Category:Cities in France"
    result["country"] = _extract_country_from_cats(cats)

    return result if result.get("continent") or result.get("country") else {}


def _extract_country_from_cats(cats: list) -> Optional[str]:
    """Extract country name from Wikipedia categories."""
    for cat in cats:
        title = cat.replace("Category:", "")
        for prefix in (
            "Cities in ",
            "Towns in ",
            "Villages in ",
            "Populated places in ",
            "Geography of ",
        ):
            if prefix in title:
                return title.split(prefix, 1)[1].split(",")[0].strip()
    return None


def _merge_hierarchy(data, enrichment):
    """Merge hierarchy fields from enrichment into data. Returns True if changed."""
    hierarchy = data.get("hierarchy") or {}
    changed = False
    for field in ("continent", "country", "region"):
        val = enrichment.get(field)
        if val and not hierarchy.get(field):
            hierarchy[field] = val
            changed = True
    if changed:
        data["hierarchy"] = hierarchy
    return changed


def _merge_historical_names(data, enrichment):
    """Merge historical names from enrichment. Returns True if changed."""
    hist = enrichment.get("historical_names")
    if not isinstance(hist, list) or not hist:
        return False
    existing = {h["name"] for h in data.get("historical_names", [])}
    changed = False
    for entry in hist:
        if isinstance(entry, dict) and entry.get("name") not in existing:
            data.setdefault("historical_names", []).append(entry)
            changed = True
    return changed


def _apply_enrichment(data, enrichment):
    """Apply enrichment data to place record."""
    if not isinstance(enrichment, dict):
        return False
    h = _merge_hierarchy(data, enrichment)
    n = _merge_historical_names(data, enrichment)
    return h or n


def _needs_enrichment(data):
    """Check if place still needs hierarchy enrichment."""
    hierarchy = data.get("hierarchy") or {}
    return not (hierarchy.get("continent") and hierarchy.get("country"))


def _try_grok(name, grok_client):
    """Try Grok enrichment. Returns dict or None."""
    try:
        return grok_client.extract_json(
            prompt=PROMPT.format(name=name),
            use_cache=True,
            cache_type="place_enrichment",
        )
    except BatchModeCollecting:
        raise
    except Exception as exc:
        logger.debug("Grok failed for %s: %s", name, exc)
        return None


def enrich_place(place_file: Path, grok_client: GrokClient) -> bool:
    """Enrich a single place file. Returns True if enriched."""
    try:
        data = json.loads(place_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to load %s: %s", place_file.name, exc)
        return False

    name = data.get("current_name") or data.get("name", "")
    if not name or not _needs_enrichment(data):
        return False

    if data.get("enrichment_status") == "not_found":
        if not _should_re_search(data):
            logger.debug("Previously searched, not found: %s", name)
            return False
        logger.info("Re-searching (stale not_found): %s", name)

    logger.info("Enriching place: %s", name)
    try:
        changed = _enrich_place_data(data, name, grok_client)
    except BatchModeCollecting:
        return False
    data["enrichment_status"] = "enriched" if changed else "not_found"
    data["last_enrichment_search"] = _today()
    write_json_with_lock(place_file, data)
    if changed:
        logger.info("  ✓ Enriched %s", name)
    return changed


def _enrich_place_data(data, name, grok_client):
    """Try Grok then Wikipedia then Grokipedia to enrich place. Returns True if changed."""
    enrichment = _try_grok(name, grok_client)
    changed = _apply_enrichment(data, enrichment) if enrichment else False
    if _needs_enrichment(data):
        wiki = _search_wikipedia(name)
        if wiki:
            changed = _apply_enrichment(data, wiki) or changed
    # Also fetch Wikipedia image + Grokipedia
    if not data.get("wikipedia_url"):
        wiki_data = _fetch_place_wikipedia_full(name)
        if wiki_data:
            if wiki_data.get("wikipedia_url"):
                data["wikipedia_url"] = wiki_data["wikipedia_url"]
                changed = True
            if wiki_data.get("image") and not data.get("images"):
                data.setdefault("images", []).insert(0, {
                    "url": wiki_data["image"],
                    "license": wiki_data.get("license", "unknown"),
                    "source": "wikipedia",
                })
                changed = True
    if not data.get("grokipedia_url"):
        grok_data = _search_grokipedia_place(name)
        if grok_data:
            data["grokipedia_url"] = grok_data
            changed = True
    return changed


def enrich_all_places(
    places_dir: Path,
    grok_client: GrokClient,
    max_places: Optional[int] = None,
    max_workers: int = 6,
) -> int:
    """Enrich all places. Returns count enriched."""
    if not places_dir.exists():
        logger.error("Directory not found: %s", places_dir)
        return 0

    files = [f for f in places_dir.glob("*.json") if f.name not in SKIP_FILES]
    if max_places:
        files = files[:max_places]

    logger.info("Enriching %d places...", len(files))

    from concurrent.futures import ThreadPoolExecutor, as_completed

    enriched = 0
    processed = 0
    total = len(files)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich_place, f, grok_client): f for f in files}
        for future in as_completed(futures):
            processed += 1
            try:
                if future.result():
                    enriched += 1
            except Exception as e:
                logger.warning("Failed to enrich place %s: %s", futures[future].stem, e)
            if processed % 10 == 0 or processed == total:
                logger.info(
                    "  Places progress: %d/%d done (%d enriched)",
                    processed,
                    total,
                    enriched,
                )

    logger.info("Place enrichment complete: %d/%d enriched", enriched, len(files))
    return enriched
