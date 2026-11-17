#!/usr/bin/env python3
"""Backfill equipment files with images from OpenSERP and Wikipedia.

Strategy:
  1. OpenSERP (if running) — real search engine results from Google/Bing/DDG,
     filtered to Wikipedia/Commons/Archive sources, then scrape actual image URLs
  2. Wikipedia API fallback — direct MediaWiki API for article images

Downloads to /filestore/equipment/<EquipmentID>/.
"""

import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_HEADERS = {"User-Agent": "SecondWorldWarAsData/2.0 (WWII research project)"}
OPENSERP_URL = "http://localhost:7001"
FILESTORE = Path("/filestore/equipment")
MAX_IMAGES = 5
SKIP_PATTERNS = {"icon", "flag", "logo", "map", "commons-logo", "wikidata", "edit"}
BAD_TITLE_PATTERNS = re.compile(r"^list of |^index of |^outline of ", re.IGNORECASE)
API_DELAY = 1.0
# Domains to accept from OpenSERP results
GOOD_DOMAINS = ("wikipedia.org", "wikimedia.org", "archive.org")
# Load blacklist
BLACKLIST = []
_bl_path = Path("config/domain_blacklist.yaml")
if _bl_path.exists():
    try:
        import yaml

        _bl_data = yaml.safe_load(_bl_path.read_text())
        BLACKLIST = [d.lower() for d in _bl_data.get("blacklist", [])]
    except Exception:
        pass


# --- OpenSERP ---


def _openserp_available() -> bool:
    """Check if OpenSERP is running."""
    try:
        r = requests.get(f"{OPENSERP_URL}/mega/search?text=test&limit=1", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _openserp_search(query: str) -> list[dict]:
    """Search via OpenSERP, return filtered results."""
    try:
        encoded = quote(query)
        url = f"{OPENSERP_URL}/mega/search?text={encoded}&engines=google,bing,duckduckgo&limit=10"
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            return []
        results = r.json() if isinstance(r.json(), list) else []
        # Filter to good domains, skip blacklisted
        filtered = []
        for item in results:
            result_url = item.get("url", "").lower()
            if any(bl in result_url for bl in BLACKLIST):
                continue
            if any(d in result_url for d in GOOD_DOMAINS):
                filtered.append(item)
        return filtered
    except Exception as e:
        logger.debug("  OpenSERP error: %s", e)
        return []


def _scrape_images_from_url(url: str) -> list[str]:
    """Scrape direct image URLs from a Wikipedia/Commons page."""
    try:
        r = requests.get(url, headers=WIKI_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        images = []
        for img in soup.find_all("img"):
            src = img.get("src", "")
            # Convert thumbnail to full-size
            if "upload.wikimedia.org" in src:
                # Thumbnail pattern: /thumb/a/ab/File.jpg/220px-File.jpg
                if "/thumb/" in src:
                    # Remove /thumb/ and the trailing /NNNpx-filename
                    parts = src.split("/thumb/")
                    if len(parts) == 2:
                        path_parts = parts[1].rsplit("/", 1)
                        src = parts[0] + "/" + path_parts[0]
                if src.startswith("//"):
                    src = "https:" + src
                if any(src.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                    images.append(src)
        return images[:MAX_IMAGES]
    except Exception as e:
        logger.debug("  Scrape failed for %s: %s", url, e)
        return []


def _openserp_find_images(data: dict) -> list[dict]:
    """Use OpenSERP to find images for equipment."""
    cn = data.get("common_name", "")
    alts = data.get("alternate_names", [])
    cat = data.get("category", "").replace("_", " ")

    # Build queries — try alternate names first
    queries = [f"{alt} WWII {cat} photo" for alt in alts[:2]]
    queries.append(f"{cn} WWII {cat} photo wikipedia")

    seen_urls = set()
    media_items = []

    for query in queries:
        if len(media_items) >= MAX_IMAGES:
            break
        results = _openserp_search(query)
        for result in results[:3]:
            page_url = result.get("url", "")
            if page_url in seen_urls:
                continue
            seen_urls.add(page_url)

            # For Wikipedia/Commons pages, scrape actual images
            image_urls = _scrape_images_from_url(page_url)
            for img_url in image_urls:
                if img_url in seen_urls or len(media_items) >= MAX_IMAGES:
                    continue
                seen_urls.add(img_url)

                source = "commons"
                if "archive.org" in img_url:
                    source = "archive"

                media_items.append(
                    {
                        "url": img_url,
                        "title": result.get("title", ""),
                        "source": source,
                        "source_page": page_url,
                    }
                )
        time.sleep(1)  # Rate limit between OpenSERP queries

    return media_items


# --- Wikipedia API ---


def _wiki_get(params: dict) -> dict | None:
    """Make a Wikipedia API request with rate limiting and retry."""
    time.sleep(API_DELAY)
    for attempt in range(3):
        try:
            r = requests.get(WIKI_API, headers=WIKI_HEADERS, timeout=15, params=params)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.debug("  Rate limited, waiting %ds...", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                logger.debug("  API error: %s", e)
    return None


def _wiki_search(query: str) -> str | None:
    """Search Wikipedia, return best article title."""
    data = _wiki_get(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 3,
        }
    )
    if not data:
        return None
    results = data.get("query", {}).get("search", [])
    for result in results:
        if not BAD_TITLE_PATTERNS.match(result["title"]):
            return result["title"]
    return None


def _wiki_page_images(title: str) -> list[str]:
    """Get image filenames from a Wikipedia article."""
    data = _wiki_get(
        {
            "action": "query",
            "titles": title,
            "prop": "images",
            "format": "json",
            "imlimit": 20,
        }
    )
    if not data:
        return []
    pages = data.get("query", {}).get("pages", {})
    images = []
    for page in pages.values():
        for img in page.get("images", []):
            fname = img["title"].lower()
            if not any(ext in fname for ext in (".jpg", ".jpeg", ".png")):
                continue
            if any(skip in fname for skip in SKIP_PATTERNS):
                continue
            images.append(img["title"])
    return images[:MAX_IMAGES]


def _wiki_image_url(filename: str) -> dict | None:
    """Get direct URL and metadata for a Wikipedia image."""
    data = _wiki_get(
        {
            "action": "query",
            "titles": filename,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "format": "json",
        }
    )
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        info = page.get("imageinfo", [{}])[0]
        url = info.get("url")
        if not url:
            return None
        meta = info.get("extmetadata", {})
        desc = meta.get("ImageDescription", {}).get("value", "")
        license_name = meta.get("LicenseShortName", {}).get("value", "See source")
        if "<" in desc:
            desc = re.sub(r"<[^>]+>", "", desc)[:200]
        return {"url": url, "description": desc, "license": license_name}
    return None


def _wiki_build_queries(data: dict) -> list[str]:
    """Build Wikipedia search queries from equipment data."""
    cn = data.get("common_name", "")
    ti = data.get("technical_identifier", "")
    alts = data.get("alternate_names", [])
    cat = data.get("category", "").replace("_", " ")
    queries = list(dict.fromkeys(alts[:3] + [f"{cn} {cat}"]))
    if ti and ti != cn:
        queries.append(f"{ti} {cn}")
    return queries


def _wiki_article_media(article_title: str) -> list[dict]:
    """Get media items from a Wikipedia article."""
    image_files = _wiki_page_images(article_title)
    media_items = []
    for img_file in image_files:
        info = _wiki_image_url(img_file)
        if not info or not info["url"]:
            continue
        media_items.append(
            {
                "url": info["url"],
                "title": img_file.replace("File:", "").rsplit(".", 1)[0],
                "source": "wikipedia",
                "license": info.get("license", "See source"),
                "description": info.get("description", "")[:200],
                "wikipedia_article": article_title,
            }
        )
    return media_items


def _wiki_find_images(data: dict) -> list[dict]:
    """Use Wikipedia API to find images for equipment."""
    cn = data.get("common_name", "")
    tried = set()
    for query in _wiki_build_queries(data):
        article_title = _wiki_search(query)
        if not article_title or article_title in tried:
            continue
        tried.add(article_title)
        media_items = _wiki_article_media(article_title)
        if media_items:
            logger.info("  📖 %s → %s", cn, article_title)
            return media_items
    return []


# --- Download & backfill ---


def _download_image(url: str, dest: Path) -> bool:
    """Download image to dest. Returns True on success."""
    try:
        r = requests.get(url, headers=WIKI_HEADERS, timeout=30, stream=True)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.warning("  Download failed: %s", e)
        return False


def _build_media_item(item: dict, eq_id: str) -> tuple[str, dict]:
    """Build local path and media item dict. Returns (local_rel, item)."""
    url = item["url"]
    ext = Path(url.split("?")[0]).suffix or ".jpg"
    filename_hash = hashlib.md5(url.encode()).hexdigest()[:8]  # nosec B324
    local_rel = f"equipment/{eq_id}/{filename_hash}{ext}"
    return local_rel, item


def _find_raw_media(data: dict, use_openserp: bool) -> list[dict]:
    """Find media via OpenSERP (if available) or Wikipedia API."""
    cn = data.get("common_name", "")
    if use_openserp:
        media = _openserp_find_images(data)
        if media:
            logger.info("  🔍 %s — %d images via OpenSERP", cn, len(media))
            return media
    return _wiki_find_images(data)


def _process_media_item(item: dict, eq_id: str, dry_run: bool) -> dict | None:
    """Download or log a single media item. Returns item if successful."""
    local_rel, item = _build_media_item(item, eq_id)
    if dry_run:
        logger.info("    🔗 %s", item["url"][:90])
        return item
    if _download_image(item["url"], FILESTORE.parent / local_rel):
        item["media_type"] = item.get("media_type", "photo")
        item["local_path"] = local_rel
        logger.info("    ✅ %s", item.get("title", item["url"].split("/")[-1])[:60])
        return item
    return None


def backfill_one(
    eq_file: Path, dry_run: bool = False, use_openserp: bool = False
) -> int:
    """Backfill media for one equipment file. Returns count of images added."""
    data = json.loads(eq_file.read_text())
    if data.get("media"):
        return 0

    raw_media = _find_raw_media(data, use_openserp)
    if not raw_media:
        logger.info("  ❌ %s — no images found", data.get("common_name", eq_file.stem))
        return 0

    eq_id = data.get("EquipmentID", "")
    media_items = [
        m for item in raw_media if (m := _process_media_item(item, eq_id, dry_run))
    ]

    if media_items and not dry_run:
        data["media"] = media_items
        eq_file.write_text(json.dumps(data, indent=2))

    return len(media_items)


def _count_result(eq_file: Path, count: int, stats: dict) -> None:
    """Update stats based on backfill result."""
    if count > 0:
        stats["added"] += count
    elif json.loads(eq_file.read_text()).get("media"):
        stats["skipped"] += 1
    else:
        stats["failed"] += 1


def main():
    dry_run = "--dry-run" in sys.argv
    equipment_dir = Path("output/equipment")

    if not equipment_dir.exists():
        logger.error("No equipment directory found")
        return

    files = sorted(
        f
        for f in equipment_dir.glob("*.json")
        if not f.name.startswith(".") and f.name != "index.json"
    )

    use_openserp = _openserp_available()
    logger.info(
        "%s OpenSERP %s",
        "✅" if use_openserp else "⚠️ ",
        (
            f"available at {OPENSERP_URL}"
            if use_openserp
            else "not available — Wikipedia API only"
        ),
    )
    logger.info(
        "Backfilling media for %d equipment files%s\n",
        len(files),
        " (DRY RUN)" if dry_run else "",
    )

    stats = {"added": 0, "skipped": 0, "failed": 0}
    for eq_file in files:
        _count_result(eq_file, backfill_one(eq_file, dry_run, use_openserp), stats)

    logger.info(
        "\nDone: %d images added, %d already had media, %d no images found",
        stats["added"],
        stats["skipped"],
        stats["failed"],
    )


if __name__ == "__main__":
    main()
