"""
Grok-based map search with site whitelisting and vision verification.

Uses Grok's search capability to find maps on whitelisted sites (from domain_blacklist.yaml),
downloads images, verifies with Grok vision API, then imports.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

import requests
import ulid
import yaml

from src.grok_client import GrokClient

logger = logging.getLogger(__name__)


def load_whitelisted_sites(
    blacklist_file: Path = Path("config/domain_blacklist.yaml"),
) -> List[str]:
    """Load whitelisted sites from config/domain_blacklist.yaml.

    Returns: List of whitelisted domains/paths
    """
    try:
        with open(blacklist_file) as f:
            data = yaml.safe_load(f)

        whitelist = data.get("whitelist", [])
        if not whitelist:
            logger.warning(
                "No whitelist found in domain_blacklist.yaml, using defaults"
            )
            return [
                "loc.gov",
                "archives.gov",
                "wikipedia.org",
            ]

        logger.info(f"Loaded {len(whitelist)} whitelisted sites from {blacklist_file}")
        return whitelist

    except Exception as e:
        logger.error(f"Failed to load whitelist from {blacklist_file}: {e}")
        return ["loc.gov", "archives.gov", "wikipedia.org"]


def search_maps_with_grok(
    place_name: str,
    date: Optional[str],
    event_context: str,
    grok_client: GrokClient,
    whitelisted_sites: List[str],
) -> List[Dict[str, Any]]:
    """Search for maps using Grok with whitelisted sites."""
    year = date.split("-")[0] if date else "1939-1945"
    sites = " OR ".join([f"site:{site}" for site in whitelisted_sites])

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt(
        "map_search", place_name=place_name, event_context=event_context, date=date
    )

    result = grok_client.extract_json(
        prompt=prompt, cache_type="grok_search_maps", temperature=0.1
    )

    if isinstance(result, list):
        return result
    return []


def download_image(image_url: str, timeout: int = 30) -> Optional[bytes]:
    """Download image from URL."""
    import requests

    try:
        # Use appropriate User-Agent based on domain
        if any(
            domain in image_url
            for domain in ["wikimedia.org", "wikipedia.org", "grokipedia.com"]
        ):
            # Sites requiring bot identification
            headers = {
                "User-Agent": "WWII-Data-Extraction-Bot/1.0 (Historical research project; contact via GitHub)"
            }
        else:
            # Standard modern browser User-Agent for other sites
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }

        response = requests.get(
            image_url, timeout=timeout, headers=headers, allow_redirects=True
        )

        if response.status_code != 200:
            return None

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            return None

        return response.content

    except Exception as e:
        logger.debug(f"Failed to download {image_url}: {e}")
        return None


def verify_map_with_vision(
    image_data: bytes,
    place_name: str,
    date: Optional[str],
    event_context: str,
    map_title: str,
    grok_client: GrokClient,
) -> tuple[bool, str]:
    """Verify map relevance using Grok vision API.

    Returns: (is_relevant, reason)
    """
    import base64
    from PIL import Image
    from io import BytesIO

    # Validate and convert image if needed
    try:
        img = Image.open(BytesIO(image_data))
        img.verify()
        img = Image.open(BytesIO(image_data))  # Reload after verify

        # Convert unsupported formats to PNG
        if img.format not in ["PNG", "JPEG", "JPG", "GIF"]:
            logger.info(f"Converting {img.format} to PNG")
            buffer = BytesIO()
            img.convert("RGB").save(buffer, format="PNG")
            image_data = buffer.getvalue()

        # Resize if too large
        size_mb = len(image_data) / (1024 * 1024)
        if size_mb > 5:
            logger.info(f"Resizing image ({size_mb:.1f}MB → target <5MB)")
            img = Image.open(BytesIO(image_data))

            # Calculate new dimensions (reduce by 50% iteratively)
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
                return False, f"Image still too large after resize ({size_mb:.1f}MB)"

            logger.info(f"Resized to {size_mb:.1f}MB")

    except Exception as e:
        return False, f"Invalid image: {e}"

    image_b64 = base64.b64encode(image_data).decode()

    from src.utils.prompt_loader import render_prompt as _rp

    prompt = _rp(
        "map_vision",
        place_name=place_name,
        event_context=event_context,
        date=date or "",
        title=map_title,
    )

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
        logger.warning(f"Vision verification failed: {e}")

    return False, "Verification failed"


def save_map_image(
    image_data: bytes,
    map_id: str,
    image_storage_path: Path,
) -> str:
    """Save image to filesystem.

    Returns: Local file path
    """
    image_storage_path.mkdir(parents=True, exist_ok=True)

    # Detect format from image data
    if image_data[:4] == b"\x89PNG":
        ext = "png"
    elif image_data[:2] == b"\xff\xd8":
        ext = "jpg"
    elif image_data[:3] == b"GIF":
        ext = "gif"
    else:
        ext = "jpg"  # default

    filename = f"{map_id}.{ext}"
    filepath = image_storage_path / filename

    with open(filepath, "wb") as f:
        f.write(image_data)

    return str(filepath)


def create_map_json(
    map_data: Dict[str, Any],
    map_id: str,
    place_name: str,
    date: Optional[str],
    event_id: Optional[str],
    event_name: Optional[str],
    sub_event_id: Optional[str],
    sub_event_name: Optional[str],
    local_image_path: str,
) -> Dict[str, Any]:
    """Create map JSON record."""
    # Detect file format from image path
    file_format = None
    if local_image_path:
        ext = Path(local_image_path).suffix.lstrip(".")
        if ext in ("jpg", "jpeg", "png", "tif", "gif", "pdf"):
            file_format = "jpg" if ext == "jpeg" else ext

    return {
        "MapID": map_id,
        "map_title": map_data["title"],
        "external_source": map_data["source"],
        "external_source_url": map_data["url"],
        "archive_id": None,
        "license": "Unknown",
        "license_url": None,
        "date_created": None,
        "creator": None,
        "EventID": event_id,
        "Event_Name": event_name,
        "Sub_eventID": sub_event_id,
        "Sub_event_Name": sub_event_name,
        "place_name": place_name,
        "PlaceMentionID": None,
        "date": date,
        "DateMentionID": None,
        "local_path": f"output/external_maps/{map_id}.json",
        "local_image_path": local_image_path,
        "source_url": map_data["image_url"],
        "file_format": file_format,
        "extracted_date": datetime.utcnow().isoformat() + "Z",
        "description": map_data.get("description"),
        "map_type": None,
        "storage_backend": "filesystem",
        "found_via": "grok_search",
        "verification_method": "grok_vision",
    }


def check_duplicate_by_url(output_dir: Path, url: str) -> bool:
    """Check if map with this URL already exists.

    Returns: True if duplicate found
    """
    for existing_file in output_dir.glob("*.json"):
        try:
            with open(existing_file) as f:
                existing = json.load(f)

            # Check both external_source_url and source_url
            if (
                existing.get("external_source_url") == url
                or existing.get("source_url") == url
            ):
                return True

        except Exception:  # nosec B112
            continue  # Skip invalid entries

    return False


def _extract_event_context(event_mentions: list) -> tuple:
    """Extract event context from place data."""
    event_context = "WWII operations"
    event_id = event_name = sub_event_id = sub_event_name = None

    if event_mentions:
        first = event_mentions[0]
        event_id = first.get("EventID")
        event_name = first.get("Event_Name", "")
        sub_event_id = first.get("Sub_eventID")
        sub_event_name = first.get("Sub_event_Name", "")
        if event_name and sub_event_name:
            event_context = f"{event_name} - {sub_event_name}"

    return event_context, event_id, event_name, sub_event_id, sub_event_name


def _process_search_result(
    result: dict,
    place_name: str,
    date: Optional[str],
    event_context: str,
    event_id: Optional[str],
    event_name: Optional[str],
    sub_event_id: Optional[str],
    sub_event_name: Optional[str],
    output_dir: Path,
    image_storage_path: Path,
    grok_client: GrokClient,
) -> bool:
    """Process a single search result. Returns True if imported."""
    title = result.get("title", "")
    image_url = result.get("image_url", "")
    page_url = result.get("url", "")

    if not image_url:
        return False

    if check_duplicate_by_url(output_dir, page_url):
        logger.info(f"   ⚠️  Duplicate: {title[:60]}...")
        return False

    logger.info(f"   🔍 {title[:60]}...")

    image_data = download_image(image_url)
    if not image_data:
        logger.info(f"   ⚠️  Failed to download image")
        return False

    is_relevant, reason = verify_map_with_vision(
        image_data, place_name, date, event_context, title, grok_client
    )

    if not is_relevant:
        logger.info(f"   ⚠️  Rejected: {reason}")
        return False

    logger.info(f"   ✅ Verified: {reason}")

    map_id = str(ulid.new())
    local_image_path = save_map_image(image_data, map_id, image_storage_path)

    map_json = create_map_json(
        result,
        map_id,
        place_name,
        date,
        event_id,
        event_name,
        sub_event_id,
        sub_event_name,
        local_image_path,
    )

    json_path = output_dir / f"{map_id}.json"
    with open(json_path, "w") as f:
        json.dump(map_json, f, indent=2)

    logger.info(f"   ✅ Imported: {map_id}")
    return True


def import_grok_search_maps(
    places_dir: Path,
    output_dir: Path,
    image_storage_path: Path,
    grok_client: GrokClient,
    max_places: Optional[int] = None,
    blacklist_file: Path = Path("domain_blacklist.yaml"),
) -> int:
    """Search and import maps using Grok search + vision verification.

    Returns: Number of maps imported
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    whitelisted_sites = load_whitelisted_sites(blacklist_file)
    logger.info(f"Using {len(whitelisted_sites)} whitelisted sites:")
    for site in whitelisted_sites:
        logger.info(f"  - {site}")

    place_files = sorted(places_dir.glob("*.json"))
    if max_places:
        place_files = place_files[:max_places]

    logger.info(f"\nSearching {len(place_files)} places with Grok search...")

    imported = 0

    for idx, place_file in enumerate(place_files, 1):
        try:
            with open(place_file) as f:
                place_data = json.load(f)

            place_name = place_data.get("current_name", "")
            if not place_name:
                continue

            logger.info(f"[{idx}/{len(place_files)}] {place_name}")

            event_context, event_id, event_name, sub_event_id, sub_event_name = (
                _extract_event_context(place_data.get("event_mentions", []))
            )

            results = search_maps_with_grok(
                place_name, None, event_context, grok_client, whitelisted_sites
            )

            if not results:
                logger.info(f"   No results from Grok search")
                continue

            logger.info(f"   Found {len(results)} potential map(s)")

            for result in results:
                if _process_search_result(
                    result,
                    place_name,
                    None,
                    event_context,
                    event_id,
                    event_name,
                    sub_event_id,
                    sub_event_name,
                    output_dir,
                    image_storage_path,
                    grok_client,
                ):
                    imported += 1

        except Exception as e:
            logger.error(f"Error processing {place_file.name}: {e}")
            continue

    return imported


def main():
    """CLI entry point."""
    from pathlib import Path

    project_root = Path(__file__).parent.parent.parent
    places_dir = project_root / "output" / "places"
    output_dir = project_root / "output" / "external_maps"
    image_storage_path = project_root / "filestore" / "external_maps"
    cache_dir = project_root / "cache" / "api"

    grok_client = GrokClient(cache_dir)

    logger.info("=" * 60)
    logger.info("Grok Search Maps (Whitelisted Sites + Vision)")
    logger.info("=" * 60)

    imported = import_grok_search_maps(
        places_dir=places_dir,
        output_dir=output_dir,
        image_storage_path=image_storage_path,
        grok_client=grok_client,
        max_places=5,  # Test with 5 places
    )

    logger.info(f"\n{'=' * 60}")
    logger.info(f"✅ Imported {imported} maps")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
