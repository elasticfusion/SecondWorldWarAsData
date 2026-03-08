"""Maps extraction from source material."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
import requests
import ulid

logger = logging.getLogger(__name__)


def _load_index(index_path: Path) -> Dict[str, str]:
    """Load index file."""
    if not index_path.exists():
        return {}
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_index(index_path: Path, index: Dict[str, str]) -> None:
    """Save index file."""
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def _find_place_for_event(
    sub_event_id: str, places_dir: Path
) -> tuple[Optional[str], Optional[str]]:
    """Find place linked to sub-event. Returns (PlaceID, place_name)."""
    index_path = places_dir / "index.json"
    if not index_path.exists():
        return None, None

    places_index = _load_index(index_path)

    for place_file_name in places_index.values():
        place_file = places_dir / place_file_name
        if not place_file.exists():
            continue

        with open(place_file, "r", encoding="utf-8") as f:
            place_data = json.load(f)

        # Check if any event mention matches this sub-event
        for mention in place_data.get("event_mentions", []):
            if mention.get("Sub_eventID") == sub_event_id:
                return place_data.get("PlaceID"), place_data.get("current_name")

    return None, None


def _find_date_for_event(
    sub_event_id: str, dates_dir: Path
) -> tuple[Optional[str], Optional[str]]:
    """Find date linked to sub-event. Returns (DateID, date_start)."""
    index_path = dates_dir / "index.json"
    if not index_path.exists():
        return None, None

    dates_index = _load_index(index_path)

    for date_file_name in dates_index.values():
        date_file = dates_dir / date_file_name
        if not date_file.exists():
            continue

        with open(date_file, "r", encoding="utf-8") as f:
            date_data = json.load(f)

        # Check if any event mention matches this sub-event
        for mention in date_data.get("event_mentions", []):
            if mention.get("Sub_eventID") == sub_event_id:
                return date_data.get("DateID"), date_data.get("date_start")

    return None, None


def _lookup_place_id(place_name: str, places_dir: Path) -> Optional[str]:
    """Look up PlaceID by fuzzy matching place name."""
    index_path = places_dir / "index.json"
    if not index_path.exists():
        return None

    places_index = _load_index(index_path)
    place_name_lower = place_name.lower()

    for place_key in places_index:
        if place_name_lower in place_key.lower():
            place_file = places_dir / places_index[place_key]
            if place_file.exists():
                with open(place_file, "r", encoding="utf-8") as f:
                    place_data = json.load(f)
                    return place_data.get("PlaceID")
    return None


def _lookup_date_id(date_str: str, dates_dir: Path) -> Optional[str]:
    """Look up DateID by date string."""
    index_path = dates_dir / "index.json"
    if not index_path.exists():
        return None

    dates_index = _load_index(index_path)
    if date_str in dates_index:
        date_file = dates_dir / dates_index[date_str]
        if date_file.exists():
            with open(date_file, "r", encoding="utf-8") as f:
                date_data = json.load(f)
                return date_data.get("DateID")
    return None


def _download_map_image(
    url: str, output_path: Path, timeout: int = 30
) -> Optional[str]:
    """Download map image from URL."""
    import requests

    # Check if already downloaded (any extension)
    for ext in ["jpg", "jpeg", "png", "tif", "tiff", "pdf"]:
        existing = output_path.with_suffix(f".{ext}")
        if existing.exists():
            logger.debug("    Already downloaded: %s", existing.name)
            return str(existing.relative_to(existing.parents[2]))

    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()

        # Determine file extension from content-type or URL
        content_type = response.headers.get("content-type", "")
        if "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        elif "png" in content_type:
            ext = "png"
        elif "tiff" in content_type or "tif" in content_type:
            ext = "tif"
        elif "pdf" in content_type:
            ext = "pdf"
        else:
            # Fallback to URL extension
            ext = url.rsplit(".", maxsplit=1)[-1].lower()
            if ext not in ["jpg", "jpeg", "png", "tif", "tiff", "pdf"]:
                ext = "jpg"  # Default

        # Save file
        output_file = output_path.with_suffix(f".{ext}")
        with open(output_file, "wb") as f:
            f.write(response.content)

        logger.debug("    Downloaded: %s", output_file.name)
        return str(output_file.relative_to(output_file.parents[2]))

    except requests.RequestException as e:
        logger.warning("    Failed to download %s: %s", url, e)
        return None


def _classify_map_type(
    description: str, sub_event_name: Optional[str], keywords: Dict[str, List[str]]
) -> Optional[str]:
    """Classify map type based on title and context."""
    text = (description + " " + (sub_event_name or "")).lower()

    for map_type, terms in keywords.items():
        if any(term in text for term in terms):
            return map_type

    return None


def _create_map_record(
    map_ulid: str,
    description: str,
    book: str,
    author: str,
    series: str,
    map_id: str,
    url: str,
    local_image_path: Optional[str],
    file_format: Optional[str],
    storage_backend: str,
    classification_keywords: Dict[str, List[str]],
    event_id: Optional[str] = None,
    event_name: Optional[str] = None,
    sub_event_id: Optional[str] = None,
    sub_event_name: Optional[str] = None,
    place_id: Optional[str] = None,
    place_name: Optional[str] = None,
    date_id: Optional[str] = None,
    date_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Create map record dictionary."""
    local_path = (
        f"output/maps/{map_ulid}.json"
        if storage_backend == "filesystem"
        else f"s3://maps/{map_ulid}.json"
    )

    map_type = _classify_map_type(description, sub_event_name, classification_keywords)

    return {
        "MapID": map_ulid,
        "map_title": description,
        "source_book": book,
        "source_author": author,
        "source_series": series if series else None,
        "page_number": None,
        "figure_number": map_id if map_id else None,
        "EventID": event_id,
        "Event_Name": event_name,
        "Sub_eventID": sub_event_id,
        "Sub_event_Name": sub_event_name,
        "place_name": place_name,
        "PlaceMentionID": place_id,
        "date": date_str,
        "DateMentionID": date_id,
        "local_path": local_path,
        "local_image_path": local_image_path,
        "source_url": url if url else None,
        "file_format": file_format,
        "extracted_date": datetime.utcnow().isoformat() + "Z",
        "description": description,
        "map_type": map_type,
        "storage_backend": storage_backend,
    }


def _save_to_s3(
    s3_client: Any,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/json",
) -> None:
    """Save data to S3."""
    s3_client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    logger.debug("  Saved to S3: s3://%s/%s", bucket, key)


def _download_image_to_s3(
    url: str,
    s3_client: Any,
    s3_bucket: str,
    s3_key_prefix: str,
    map_ulid: str,
    timeout: int,
) -> tuple[Optional[str], Optional[str]]:
    """Download image and upload to S3. Returns (s3_path, file_format)."""
    import requests

    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        image_data = response.content

        # Determine format
        content_type = response.headers.get("content-type", "")
        if "jpeg" in content_type or "jpg" in content_type:
            file_format = "jpg"
            content_type = "image/jpeg"
        elif "png" in content_type:
            file_format = "png"
            content_type = "image/png"
        elif "tiff" in content_type or "tif" in content_type:
            file_format = "tif"
            content_type = "image/tiff"
        elif "pdf" in content_type:
            file_format = "pdf"
            content_type = "application/pdf"
        else:
            file_format = "jpg"
            content_type = "image/jpeg"

        # Save to S3
        s3_key = f"{s3_key_prefix}images/{map_ulid}.{file_format}"
        _save_to_s3(s3_client, s3_bucket, s3_key, image_data, content_type)
        s3_path = f"s3://{s3_bucket}/{s3_key}"
        return s3_path, file_format

    except requests.RequestException as e:
        logger.warning("    Failed to download %s: %s", url, e)
        return None, None


def _download_image(
    url: str,
    storage_backend: str,
    image_storage: Optional[Path],
    s3_client: Optional[Any],
    s3_bucket: Optional[str],
    s3_prefix: str,
    map_ulid: str,
    timeout: int,
) -> tuple[Optional[str], Optional[str], int]:
    """Download image to appropriate backend. Returns (path, format, downloaded_count)."""
    if storage_backend == "filesystem" and image_storage:
        image_path = image_storage / map_ulid
        local_image_path = _download_map_image(url, image_path, timeout)
        if local_image_path:
            file_format: Optional[str] = local_image_path.rsplit(".", maxsplit=1)[-1]
            return local_image_path, file_format, 1
    elif storage_backend == "s3" and s3_client and s3_bucket:
        s3_path, s3_format = _download_image_to_s3(
            url, s3_client, s3_bucket, s3_prefix, map_ulid, timeout
        )
        if s3_path:
            return s3_path, s3_format, 1

    return None, None, 0


def _save_map_record(
    map_record: Dict[str, Any],
    map_filename: str,
    storage_backend: str,
    maps_dir: Optional[Path],
    s3_client: Optional[Any],
    s3_bucket: Optional[str],
    s3_prefix: str,
) -> None:
    """Save map record to appropriate backend."""
    if storage_backend == "filesystem" and maps_dir:
        map_file = maps_dir / map_filename
        with open(map_file, "w", encoding="utf-8") as f:
            json.dump(map_record, f, indent=2)
    elif storage_backend == "s3" and s3_client and s3_bucket:
        s3_key = f"{s3_prefix}metadata/{map_filename}"
        map_json = json.dumps(map_record, indent=2).encode("utf-8")
        _save_to_s3(s3_client, s3_bucket, s3_key, map_json)


def _process_map(
    map_data: Dict[str, Any],
    book: str,
    author: str,
    series: str,
    maps_dir: Optional[Path],
    maps_index: Dict[str, str],
    download_images: bool,
    storage_backend: str,
    classification_keywords: Dict[str, List[str]],
    s3_client: Optional[Any] = None,
    s3_bucket: Optional[str] = None,
    s3_prefix: str = "maps/",
    image_storage: Optional[Path] = None,
    timeout: int = 30,
    event_id: Optional[str] = None,
    event_name: Optional[str] = None,
    sub_event_id: Optional[str] = None,
    sub_event_name: Optional[str] = None,
    place_id: Optional[str] = None,
    place_name: Optional[str] = None,
    date_id: Optional[str] = None,
    date_str: Optional[str] = None,
) -> tuple[int, int]:
    """Process a single map. Returns (new_maps, downloaded)."""
    map_id = map_data.get("map_id", "")
    url = map_data.get("url", "")
    description = map_data.get("description", "")
    map_ulid = str(ulid.new())

    # Download image if enabled
    local_image_path = None
    file_format = None
    downloaded = 0

    if download_images and url:
        local_image_path, file_format, downloaded = _download_image(
            url,
            storage_backend,
            image_storage,
            s3_client,
            s3_bucket,
            s3_prefix,
            map_ulid,
            timeout,
        )

    # Create map record
    map_record = _create_map_record(
        map_ulid,
        description,
        book,
        author,
        series,
        map_id,
        url,
        local_image_path,
        file_format,
        storage_backend,
        classification_keywords,
        event_id,
        event_name,
        sub_event_id,
        sub_event_name,
        place_id,
        place_name,
        date_id,
        date_str,
    )

    # Save map record
    map_filename = f"{map_ulid}.json"
    _save_map_record(
        map_record,
        map_filename,
        storage_backend,
        maps_dir,
        s3_client,
        s3_bucket,
        s3_prefix,
    )

    # Update index
    maps_index[map_ulid] = map_filename
    logger.debug("  Created map: %s (%s)", description, map_ulid)

    return 1, downloaded


def _setup_storage_backend(
    maps_config: Dict[str, Any], output_dir: Path
) -> tuple[str, Optional[Path], Optional[Any], Optional[str], str]:
    """Setup storage backend. Returns (backend, maps_dir, s3_client, s3_bucket, s3_prefix)."""
    storage_backend = maps_config.get("storage_backend", "filesystem")

    if storage_backend == "filesystem":
        maps_dir = output_dir / "maps"
        maps_dir.mkdir(exist_ok=True)
        return storage_backend, maps_dir, None, None, ""

    # S3 backend
    s3_bucket = maps_config.get("s3_bucket")
    if not s3_bucket:
        logger.error("S3 backend selected but s3_bucket not configured")
        return storage_backend, None, None, None, ""

    s3_region = maps_config.get("s3_region", "us-east-1")
    s3_prefix = maps_config.get("s3_prefix", "maps/")
    s3_client = boto3.client("s3", region_name=s3_region)
    logger.info("Using S3 backend: s3://%s/%s", s3_bucket, s3_prefix)

    return storage_backend, None, s3_client, s3_bucket, s3_prefix


def _setup_image_storage(
    maps_config: Dict[str, Any], storage_backend: str
) -> tuple[bool, Optional[Path], int]:
    """Setup image download config. Returns (download_images, image_storage, timeout)."""
    download_images = maps_config.get("download_images", False)
    timeout = maps_config.get("download_timeout", 30)

    if not download_images:
        return False, None, timeout

    if storage_backend == "filesystem":
        image_storage = Path(
            maps_config.get("image_storage_path", "output/maps_images")
        )
        image_storage.mkdir(parents=True, exist_ok=True)
        return True, image_storage, timeout

    # S3 backend - no local storage needed
    return True, None, timeout


def _extract_maps_from_text(text: str) -> list[tuple[str, str]]:
    """Extract map references from text. Returns list of (map_id, url)."""
    maps = []
    seen_urls = set()

    # Pattern: [Map X](url) - with "Map" in description
    for match in re.finditer(
        r"\[Map\s+([^\]]+)\]\((https?://[^\)]+)\)", text, re.IGNORECASE
    ):
        map_id = match.group(1).strip()
        url = match.group(2)

        if url not in seen_urls:
            maps.append((map_id, url))
            seen_urls.add(url)

    return maps


def _process_event_files(
    output_dir: Path,
    maps_dir: Optional[Path],
    maps_index: Dict[str, str],
    download_images: bool,
    storage_backend: str,
    classification_keywords: Dict[str, List[str]],
    s3_client: Optional[Any],
    s3_bucket: Optional[str],
    s3_prefix: str,
    image_storage: Optional[Path],
    timeout: int,
    places_dir: Path,
    dates_dir: Path,
) -> tuple[int, int, int]:
    """Process all event files. Returns (total_maps, new_maps, downloaded)."""
    total_maps = 0
    new_maps = 0
    downloaded = 0

    # Load processed events registry (store in output/maps/)
    maps_output_dir = output_dir / "maps"
    maps_output_dir.mkdir(parents=True, exist_ok=True)
    processed_registry = maps_output_dir / ".processed_events.json"

    if processed_registry.exists():
        with open(processed_registry) as f:
            processed = json.load(f)
    else:
        processed = {}

    for event_file in output_dir.rglob("*-event.json"):
        # Check if already processed
        event_key = str(event_file)
        if event_key in processed:
            logger.debug("Already processed %s, skipping", event_file)
            continue

        logger.debug("Processing %s", event_file)

        # Mark as processed immediately to survive interruptions
        processed[event_key] = True
        with open(processed_registry, "w") as f:
            json.dump(processed, f, indent=2)

        with open(event_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        event_data = data.get("Event")
        if not event_data:
            continue

        # Load corresponding parsed file for book/author info
        parsed_file = event_file.parent / event_file.name.replace(
            "-event.json", "-parsed.json"
        )
        if not parsed_file.exists():
            continue

        with open(parsed_file, "r", encoding="utf-8") as f:
            parsed_data = json.load(f)

        book = parsed_data.get("book", "Unknown")
        author = parsed_data.get("author", "Unknown")
        series = parsed_data.get("series", "")
        chapter_title = parsed_data.get("chapter_title", "")

        event_id = event_data.get("EventID")
        event_name = event_data.get("Event_Name") or chapter_title

        # Process each sub-event
        for sub_event in event_data.get("Sub-events", []):
            sub_event_id = sub_event.get("Sub-eventID")
            sub_event_summary = sub_event.get("Sub-event_summary", "")
            # Use first sentence of summary as name
            sub_event_name = sub_event.get("Sub_event_Name") or (
                sub_event_summary.split(".")[0] if sub_event_summary else None
            )

            # Find place and date linked to this sub-event
            place_id, place_name = _find_place_for_event(sub_event_id, places_dir)
            date_id, date_str = _find_date_for_event(sub_event_id, dates_dir)

            # Extract maps from fulltext
            fulltext = sub_event.get("Sub-event_fulltext", {})
            all_text = " ".join(fulltext.values())

            maps = _extract_maps_from_text(all_text)
            if not maps:
                continue

            for map_id, url in maps:
                total_maps += 1
                new, down = _process_map(
                    {"map_id": map_id, "url": url, "description": f"Map {map_id}"},
                    book,
                    author,
                    series,
                    maps_dir,
                    maps_index,
                    download_images,
                    storage_backend,
                    classification_keywords,
                    s3_client,
                    s3_bucket,
                    s3_prefix,
                    image_storage,
                    timeout,
                    event_id,
                    event_name,
                    sub_event_id,
                    sub_event_name,
                    place_id,
                    place_name,
                    date_id,
                    date_str,
                )
                new_maps += new
                downloaded += down

    return total_maps, new_maps, downloaded


def extract_maps(
    parsed_dir: Path,  # pylint: disable=unused-argument
    output_dir: Path,
    places_dir: Path,  # pylint: disable=unused-argument
    dates_dir: Path,  # pylint: disable=unused-argument
    config: Dict[str, Any],
) -> None:
    """Extract maps from Phase 1 parsed documents."""
    maps_config = config.get("maps", {})
    if not maps_config.get("enabled", False):
        logger.info("Maps extraction disabled in config")
        return

    logger.info("Starting maps extraction from source material")

    # Get classification keywords
    classification_keywords = maps_config.get(
        "classification_keywords",
        {
            "tactical": [
                "attack",
                "assault",
                "advance",
                "retreat",
                "defense",
                "battle",
            ],
            "strategic": [
                "campaign",
                "theater",
                "front",
                "invasion",
                "offensive",
                "deployment",
            ],
            "logistical": ["supply", "logistics", "transport", "route", "port"],
            "political": ["border", "territory", "zone", "occupation", "political"],
        },
    )

    # Setup storage backend
    storage_backend, maps_dir, s3_client, s3_bucket, s3_prefix = _setup_storage_backend(
        maps_config, output_dir
    )
    if storage_backend == "s3" and not s3_bucket:
        return  # Error already logged

    # Setup image download
    download_images, image_storage, timeout = _setup_image_storage(
        maps_config, storage_backend
    )

    # Load index (filesystem only)
    index_path = (
        output_dir / "maps" / "index.json" if storage_backend == "filesystem" else None
    )
    maps_index = _load_index(index_path) if index_path else {}

    # Process all event files
    total_maps, new_maps, downloaded = _process_event_files(
        output_dir,
        maps_dir,
        maps_index,
        download_images,
        storage_backend,
        classification_keywords,
        s3_client,
        s3_bucket,
        s3_prefix,
        image_storage,
        timeout,
        places_dir,
        dates_dir,
    )

    # Save index (filesystem only)
    if index_path:
        _save_index(index_path, maps_index)

    if download_images:
        logger.info(
            "Maps extraction complete: %d new maps, %d images downloaded",
            new_maps,
            downloaded,
        )
    else:
        logger.info(
            "Maps extraction complete: %d new maps (total: %d)", new_maps, total_maps
        )
