"""Equipment media operations — extracted from equipment.py for readability."""

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import ulid
from typing import Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from src.grok_client import GrokClient

from src.utils.http_pool import get_session
from src.extraction.equipment_ext.enrichment import (
    _enrich_equipment_data,
    _extract_year_from_date,
    _merge_enriched_data,
)

logger = logging.getLogger(__name__)


def _verify_media_with_vision(
    image_data: bytes,
    equipment_name: str,
    equipment_category: str,
    media_title: str,
    grok_client: GrokClient,
) -> tuple[bool, str]:
    """Verify media relevance using Grok vision API.

    Returns: (is_relevant, reason)
    """
    import base64
    from io import BytesIO

    from PIL import Image

    # Validate and convert image if needed
    try:
        img = Image.open(BytesIO(image_data))
        img.verify()
        img = Image.open(BytesIO(image_data))  # Reload after verify

        # Convert unsupported formats to PNG
        if img.format not in ["PNG", "JPEG", "JPG", "GIF"]:
            logger.debug("Converting %s to PNG", img.format)
            buffer = BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            image_data = buffer.getvalue()

        # Resize if too large
        size_mb = len(image_data) / (1024 * 1024)
        if size_mb > 5:
            logger.debug("Resizing image (%.1fMB → target <5MB)", size_mb)
            img = Image.open(BytesIO(image_data))

            scale = 0.7
            while size_mb > 5 and scale > 0.1:
                new_size = (int(img.width * scale), int(img.height * scale))
                resized = img.resize(new_size, Image.Resampling.LANCZOS)

                buffer = BytesIO()
                resized.save(buffer, format="PNG", optimize=True)
                image_data = buffer.getvalue()
                size_mb = len(image_data) / (1024 * 1024)
                scale -= 0.1

            if size_mb > 5:
                return False, f"Image too large ({size_mb:.1f}MB)"

    except Exception as e:
        return False, f"Invalid image: {e}"

    image_b64 = base64.b64encode(image_data).decode()

    prompt = f"""Analyze this image to verify it's relevant WWII equipment media.

Expected:
- Equipment: {equipment_name}
- Category: {equipment_category}
- Title claims: {media_title}

Verify:
1. Does this show {equipment_name} or related equipment?
2. Is it from WWII era (1935-1950)?
3. Is it a photo, diagram, or document (not unrelated content)?
4. Does it match the category: {equipment_category}?

Respond with ONLY a JSON object:
{{"is_relevant": true or false, "reason": "Brief explanation"}}
"""

    try:
        result = grok_client.extract_json_with_image_base64(
            prompt=prompt,
            image_base64=image_b64,
            cache_type="vision_verification",
            temperature=0.0,
        )

        if isinstance(result, dict):
            return result.get("is_relevant", False), result.get("reason", "Unknown")

    except Exception as e:
        logger.warning("Vision verification failed: %s", e)

    return False, "Verification failed"


def _add_downloaded_media(
    equipment_data: Dict[str, Any],
    media_list: list,
    common_name: str,
    grok_client: GrokClient,
    verify_media_with_vision: bool,
) -> None:
    """Download and add media to equipment data."""
    if not media_list:
        return

    media_dir = Path("filestore/equipment")
    downloaded_media = _download_and_store_media(
        media_list,
        common_name,
        equipment_data["category"],
        media_dir,
        grok_client,
        verify_with_vision=verify_media_with_vision,
    )

    if downloaded_media:
        equipment_data["media"] = downloaded_media
        logger.info("  Added %s verified media items", len(downloaded_media))


def _enrich_and_add_media(
    equipment_data: Dict[str, Any],
    common_name: str,
    grok_client: GrokClient,
    verify_media_with_vision: bool = True,
    sub_event_id: Optional[str] = None,
    dates_index: Optional[Dict[str, Dict[str, str]]] = None,
) -> None:
    """Enrich equipment data and add media files.

    Args:
        equipment_data: Equipment data dict (modified in place)
        common_name: Equipment common name
        grok_client: Grok API client
        verify_media_with_vision: Verify media relevance with Grok vision API
        sub_event_id: Sub-event ID to look up date
        dates_index: Index of dates by Sub-eventID
    """
    # Get year from date if available
    year = _extract_year_from_date(sub_event_id, dates_index)

    # Enrich equipment data
    logger.info("Enriching equipment data for: %s", common_name)
    enriched = _enrich_equipment_data(
        common_name,
        equipment_data.get("technical_identifier"),
        equipment_data["category"],
        grok_client,
    )
    _merge_enriched_data(equipment_data, enriched)

    # Extract and download media
    media_list = _extract_media(
        common_name,
        equipment_data.get("technical_identifier"),
        equipment_data["category"],
        grok_client,
        use_openserp=True,
        year=year,
    )
    _add_downloaded_media(
        equipment_data, media_list, common_name, grok_client, verify_media_with_vision
    )


def _download_and_store_media(
    media_list: List[Dict[str, Any]],
    equipment_name: str,
    equipment_category: str,
    media_dir: Path,
    grok_client: GrokClient,
    verify_with_vision: bool = True,
) -> List[Dict[str, Any]]:
    """Download media files and add local paths with deduplication.

    Args:
        media_list: List of media items with URLs
        equipment_name: Equipment name for logging
        equipment_category: Equipment category for verification
        media_dir: Base media directory
        grok_client: Grok API client for vision verification
        verify_with_vision: Whether to verify images with Grok vision API

    Returns:
        Media list with local_path added to verified/downloaded items (duplicates removed)
    """
    downloaded_media = []
    image_hashes: Dict[str, Tuple[str, str]] = {}  # hash -> (local_path, title)

    for media_item in media_list:
        local_path = _download_media_file(
            media_item,
            equipment_name,
            equipment_category,
            media_dir,
            grok_client,
            verify_with_vision,
        )
        if local_path:
            # Check for duplicate images using perceptual hash
            if media_item.get("media_type") == "photo":
                full_path = media_dir.parent / local_path
                img_hash = _compute_image_hash(full_path)

                if img_hash and img_hash in image_hashes:
                    # Duplicate found - remove the file
                    existing_path, existing_title = image_hashes[img_hash]
                    logger.info(
                        "  🗑️  Duplicate image removed: %s (same as %s)",
                        media_item.get("title", "Unknown"),
                        existing_title,
                    )
                    try:
                        full_path.unlink()
                        # Remove empty parent directory
                        if full_path.parent.exists() and not any(
                            full_path.parent.iterdir()
                        ):
                            full_path.parent.rmdir()
                    except Exception as e:
                        logger.debug("Failed to remove duplicate: %s", e)
                    continue
                elif img_hash:
                    # New unique image
                    image_hashes[img_hash] = (
                        local_path,
                        media_item.get("title", "Unknown"),
                    )

            media_item["local_path"] = local_path
            downloaded_media.append(media_item)
        else:
            logger.debug("Skipped media: %s", media_item.get("title", "Unknown"))

    return downloaded_media


def _compute_image_hash(image_path: Path) -> Optional[str]:
    """Compute perceptual hash for image deduplication.

    Args:
        image_path: Path to image file

    Returns:
        Hash string or None if failed
    """
    try:
        from PIL import Image
        import imagehash

        with Image.open(image_path) as img:
            # Use average hash (fast and effective for duplicates)
            return str(imagehash.average_hash(img))
    except Exception as e:
        logger.debug("Failed to compute hash for %s: %s", image_path.name, e)
        return None


def _determine_file_extension(response, url: str) -> str:
    """Determine file extension from content-type or URL."""
    content_type = response.headers.get("content-type", "")

    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    elif "png" in content_type:
        return ".png"
    elif "gif" in content_type:
        return ".gif"
    elif "webp" in content_type:
        return ".webp"
    elif "pdf" in content_type:
        return ".pdf"
    elif "mp4" in content_type or "video" in content_type:
        return ".mp4"
    else:
        # Fallback to URL extension
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return Path(parsed.path).suffix or ".jpg"


def _verify_and_save_media(
    response,
    filepath: Path,
    equipment_dir: Path,
    media_item: Dict[str, Any],
    equipment_name: str,
    equipment_category: str,
    grok_client: GrokClient,
    verify_with_vision: bool,
) -> bool:
    """Verify media with vision API and save if relevant."""
    # Verify with vision API if enabled
    if verify_with_vision and media_item.get("media_type") == "photo":
        is_relevant, reason = _verify_media_with_vision(
            response.content,
            equipment_name,
            equipment_category,
            media_item.get("title", ""),
            grok_client,
        )
        if not is_relevant:
            logger.info("  ⚠️  Rejected: %s", reason)
            return False
        logger.info("  ✅ Verified: %s", reason)

    # Create directory and save file
    equipment_dir.mkdir(parents=True, exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(response.content)

    logger.debug("Downloaded media: %s", filepath.name)
    return True


def _cleanup_empty_directory(equipment_dir: Path) -> None:
    """Clean up empty directory if download failed."""
    if equipment_dir.exists() and not any(equipment_dir.iterdir()):
        try:
            equipment_dir.rmdir()
            logger.debug("Cleaned up empty directory: %s", equipment_dir.name)
        except Exception:
            logger.debug("Could not remove empty directory: %s", equipment_dir.name)


def _download_media_file(
    media_item: Dict[str, Any],
    equipment_name: str,
    equipment_category: str,
    media_dir: Path,
    grok_client: GrokClient,
    verify_with_vision: bool = True,
) -> Optional[str]:
    """Download media file to local storage with vision verification.

    Args:
        media_item: Media item with URL
        equipment_name: Equipment name for subdirectory
        equipment_category: Equipment category for verification
        media_dir: Base media directory (/filestore)
        grok_client: Grok API client for vision verification
        verify_with_vision: Whether to verify images with Grok vision API

    Returns:
        Relative path to downloaded file or None
    """
    import requests

    url = media_item.get("url")
    if not url:
        return None

    # Create equipment subdirectory
    media_id = str(ulid.new())
    equipment_dir = media_dir / media_id

    try:
        # Download file with User-Agent header
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; WWII-Research-Bot/1.0; +https://github.com/yourusername/project)"
        }
        session = get_session()
        response = session.get(url, timeout=30, headers=headers, allow_redirects=True)
        response.raise_for_status()

        # Determine file extension and generate filename
        ext = _determine_file_extension(response, url)
        filename = f"{media_id}{ext}"
        filepath = equipment_dir / filename

        # Check if already downloaded
        if filepath.exists():
            logger.debug("Media already downloaded: %s", filename)
            return str(filepath.relative_to(media_dir.parent))

        # Verify and save
        if not _verify_and_save_media(
            response,
            filepath,
            equipment_dir,
            media_item,
            equipment_name,
            equipment_category,
            grok_client,
            verify_with_vision,
        ):
            return None

        return str(filepath.relative_to(media_dir.parent))

    except requests.RequestException as e:
        logger.warning("Failed to download %s: %s", url, e)
        return None
    except Exception as e:
        logger.debug("Media download error: %s", e)
        return None
    finally:
        _cleanup_empty_directory(equipment_dir)


def _extract_media(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
    use_openserp: bool = True,
    year: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract media using OpenSERP (preferred) or Wikipedia fallback.

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client
        use_openserp: Try OpenSERP first
        year: Year for temporal filtering (e.g., "1944")

    Returns:
        List of media items with URLs
    """
    media_list = []

    # Try OpenSERP first (real search engines, no hallucinations)
    if use_openserp:
        media_list = _extract_media_with_openserp(
            common_name, technical_identifier, category, grok_client, year
        )
        if media_list:
            logger.debug("Using OpenSERP media for %s", common_name)
            return media_list

    # Fallback to Wikipedia/Grokipedia
    media_list = _extract_media_from_wikipedia(
        common_name, technical_identifier, category, grok_client
    )
    if media_list:
        logger.debug("Using Wikipedia media for %s", common_name)

    return media_list


def _extract_image_urls_from_page(
    page_url: str, equipment_name: str, grok_client: GrokClient
) -> List[str]:
    """Extract actual image URLs from a wiki page using Grok.

    Args:
        page_url: URL of the wiki page
        equipment_name: Equipment name for context
        grok_client: Grok API client

    Returns:
        List of direct image URLs
    """
    try:
        # Fetch page content with standard browser headers
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        session = get_session()
        response = session.get(
            page_url, timeout=30, headers=headers, allow_redirects=True
        )
        response.raise_for_status()
        page_content = response.text

        # Ask Grok to extract image URLs
        prompt = f"""Extract direct image URLs from this Wikipedia/Wikimedia page about {equipment_name}.

Page URL: {page_url}
Page content (first 8000 chars):
{page_content[:8000]}

Find URLs that point to actual image files (jpg, png, svg, etc.), not wiki pages.
Look for:
- URLs in src attributes of <img> tags
- URLs in href attributes linking to File: pages
- Full resolution image URLs (not thumbnails if possible)

Return ONLY a JSON array of direct image URLs:
["https://upload.wikimedia.org/...", ...]

If no images found, return empty array: []
"""

        result = grok_client.extract_json(
            prompt=prompt, cache_type="equipment_image_extraction", temperature=0.0
        )

        if isinstance(result, list):
            # Filter for valid image URLs
            image_urls = []
            for url in result:
                if isinstance(url, str) and url.startswith("http"):
                    # Ensure it's a direct image URL
                    if any(
                        ext in url.lower()
                        for ext in [".jpg", ".jpeg", ".png", ".svg", ".gif", ".webp"]
                    ):
                        image_urls.append(url)
            return image_urls

    except Exception as e:
        logger.debug("Failed to extract images from %s: %s", page_url, e)

    return []


def _build_search_query(
    common_name: str, technical_identifier: Optional[str], year: Optional[str]
) -> str:
    """Build search query for OpenSERP."""
    identifier = technical_identifier or common_name
    year_str = year if year else "1939-1945"
    return f"{identifier} {common_name} WWII {year_str} photo wikipedia commons"


def _run_openserp_search(search_query: str) -> list:
    """Run OpenSERP search and return results."""
    import subprocess  # nosec B404

    try:
        result = subprocess.run(  # nosec B603 B404
            ["./tools/search_media", search_query],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if result.returncode != 0:
            logger.debug("OpenSERP search failed: %s", result.stderr)
            return []

        return json.loads(result.stdout)

    except FileNotFoundError:
        logger.debug("OpenSERP tool not found, skipping")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("OpenSERP search timed out")
        return []
    except json.JSONDecodeError as e:
        logger.debug("Failed to parse OpenSERP response: %s", e)
        return []


def _extract_images_from_pages(
    page_results: list, common_name: str, grok_client: GrokClient
) -> list:
    """Extract image URLs from wiki pages."""
    media_list = []

    for page in page_results[:3]:  # Limit to first 3 pages
        if not isinstance(page, dict) or "url" not in page:
            continue

        page_url = page["url"]
        image_urls = _extract_image_urls_from_page(page_url, common_name, grok_client)

        for img_url in image_urls[:2]:  # Max 2 images per page
            media_list.append(
                {
                    "media_type": "photo",
                    "url": img_url,
                    "title": page.get("title", ""),
                    "source": page.get("source", "unknown"),
                    "license": "See source",
                    "description": f"From {page_url}",
                }
            )

    return media_list


def _extract_media_with_openserp(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
    year: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extract media URLs using OpenSERP (real search engines).

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client for extracting images from pages
        year: Year for temporal filtering (e.g., "1944")

    Returns:
        List of media items with direct image URLs
    """
    search_query = _build_search_query(common_name, technical_identifier, year)

    try:
        page_results = _run_openserp_search(search_query)
        if not page_results:
            return []

        logger.debug(
            "Found %s wiki pages via OpenSERP for %s", len(page_results), common_name
        )

        media_list = _extract_images_from_pages(page_results, common_name, grok_client)
        logger.debug("Extracted %s image URLs from wiki pages", len(media_list))

        return media_list

    except Exception as e:
        logger.debug("OpenSERP search error: %s", e)
        return []


def _extract_media_from_wikipedia(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
) -> List[Dict[str, Any]]:
    """Extract images from the actual Wikipedia article for this equipment.

    Finds the Wikipedia page, gets its images via the API, then returns
    real download URLs. Grok vision validates relevance downstream.
    """
    identifier = technical_identifier or common_name
    session = get_session()
    headers = {"User-Agent": "WWII-Research-Bot/1.0 (academic research)"}

    try:
        # Step 1: Find the Wikipedia article
        search_resp = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": identifier,
                "srlimit": "1",
            },
            timeout=15,
            headers=headers,
        )
        search_resp.raise_for_status()
        results = search_resp.json().get("query", {}).get("search", [])
        if not results:
            logger.debug("No Wikipedia article for %s", identifier)
            return []

        page_title = results[0]["title"]

        # Step 2: Get images from that article
        img_resp = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": page_title,
                "prop": "images",
                "imlimit": "20",
            },
            timeout=15,
            headers=headers,
        )
        img_resp.raise_for_status()
        pages = img_resp.json().get("query", {}).get("pages", {})
        image_titles = []
        for page in pages.values():
            for img in page.get("images", []):
                title = img.get("title", "")
                # Skip icons/logos/commons junk
                if any(
                    skip in title.lower()
                    for skip in [
                        "icon",
                        "logo",
                        "flag",
                        "symbol",
                        "commons-",
                        "edit-",
                        "question_book",
                        "wikiproject",
                        "padlock",
                        "ambox",
                    ]
                ):
                    continue
                if title.lower().endswith((".jpg", ".jpeg", ".png", ".svg")):
                    image_titles.append(title)

        if not image_titles:
            logger.debug("No images on Wikipedia page for %s", identifier)
            return []

        # Step 3: Get actual file URLs from Commons (batch up to 5)
        file_resp = session.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": "|".join(image_titles[:5]),
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime",
                "iiurlwidth": "1280",
            },
            timeout=15,
            headers=headers,
        )
        file_resp.raise_for_status()
        file_pages = file_resp.json().get("query", {}).get("pages", {})

        media_list = []
        for fp in file_pages.values():
            info = (fp.get("imageinfo") or [{}])[0]
            mime = info.get("mime", "")
            if not mime.startswith("image/"):
                continue
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            meta = info.get("extmetadata", {})
            media_list.append(
                {
                    "media_type": "photo",
                    "url": url,
                    "title": fp.get("title", "").replace("File:", ""),
                    "source": "commons",
                    "license": meta.get("LicenseShortName", {}).get("value", "Unknown"),
                    "description": (
                        meta.get("ImageDescription", {}).get("value", "") or ""
                    )[:200],
                }
            )

        logger.debug(
            "Found %s images from Wikipedia article '%s'", len(media_list), page_title
        )
        return media_list
    except Exception as e:
        logger.warning("Wikipedia media lookup failed for %s: %s", common_name, e)
        return []
