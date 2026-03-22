"""Image extraction from source material.

Extracts images parsed in Phase 1, creates spec-compliant image entities
with event/place/date linking, and optionally downloads image files.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
import ulid as ulid_mod

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 30
_USER_AGENT = "SecondWorldWarAsData/2.0 (WWII research project)"


def _classify_content_type(alt_text: str, url: str) -> str:
    """Classify image content type from alt text and URL."""
    text = f"{alt_text} {url}".lower()
    if any(w in text for w in ("map ", "map_", "/maps/")):
        return "map"
    if any(w in text for w in ("diagram", "chart", "plan ", "sketch")):
        return "diagram"
    return "photograph"


def _find_sub_event_for_image(img: dict, event_data: dict) -> Optional[Dict[str, str]]:
    """Find which sub-event contains this image. Returns event context dict."""
    url = img.get("url", "")
    alt = img.get("alt_text", "")
    resource_id = img.get("resource_id", "")

    event = event_data.get("Event", {})
    event_id = event.get("EventID", "")
    event_name = event_data.get("Chapter", "")

    for se in event.get("Sub-events", []):
        fulltext = json.dumps(se.get("Sub-event_fulltext", {}))
        matched = (
            (url and url in fulltext)
            or (resource_id and resource_id in fulltext)
            or (alt and len(alt) > 10 and alt in fulltext)
        )
        if matched:
            return {
                "EventID": event_id,
                "Event_Name": event_name,
                "Sub-eventID": se.get("Sub-eventID", ""),
                "Sub-event_Name": se.get("Sub-event_summary", ""),
            }
    return None


def _find_linked_place(
    sub_event_id: str, places_dir: Path
) -> Tuple[Optional[str], Optional[str]]:
    """Find PlaceMentionID and place_name for a sub-event."""
    index_file = places_dir / "index.json"
    if not index_file.exists():
        return None, None

    index = json.loads(index_file.read_text())
    for fname in index.values():
        fpath = places_dir / fname
        if not fpath.exists():
            continue
        data = json.loads(fpath.read_text())
        for m in data.get("event_mentions", []):
            if m.get("Sub_eventID") == sub_event_id:
                mention_id = m.get("PlaceMentionID") or m.get("MentionID")
                return mention_id, data.get("current_name")
    return None, None


def _find_linked_date(
    sub_event_id: str, dates_dir: Path
) -> Tuple[Optional[str], Optional[str]]:
    """Find DateMentionID and date for a sub-event."""
    index_file = dates_dir / "index.json"
    if not index_file.exists():
        return None, None

    index = json.loads(index_file.read_text())
    for fname in index.values():
        fpath = dates_dir / fname
        if not fpath.exists():
            continue
        data = json.loads(fpath.read_text())
        for m in data.get("event_mentions", []):
            if m.get("Sub_eventID") == sub_event_id:
                mention_id = m.get("DateMentionID") or m.get("MentionID")
                date_val = data.get("date_start") or data.get("date")
                return mention_id, date_val
    return None, None


def _download_image(
    url: str, image_id: str, storage_dir: Path
) -> Tuple[Optional[str], Optional[str]]:
    """Download image. Returns (local_path, file_format) or (None, None)."""
    try:
        headers = {"User-Agent": _USER_AGENT}
        resp = requests.get(
            url, timeout=_DOWNLOAD_TIMEOUT, headers=headers, allow_redirects=True
        )
        if resp.status_code != 200:
            return None, None

        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and len(resp.content) < 1000:
            return None, None

        # Determine extension
        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "tif" in content_type:
            ext = "tif"
        elif "gif" in content_type:
            ext = "gif"
        elif url.lower().endswith(".png"):
            ext = "png"
        elif url.lower().endswith(".tif") or url.lower().endswith(".tiff"):
            ext = "tif"

        storage_dir.mkdir(parents=True, exist_ok=True)
        filepath = storage_dir / f"{image_id}.{ext}"
        filepath.write_bytes(resp.content)
        return str(filepath), ext

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("Failed to download %s: %s", url, exc)
        return None, None


def _create_image_record(
    img: dict,
    image_id: str,
    event_ctx: Optional[Dict[str, str]],
    place_mention_id: Optional[str],
    place_name: Optional[str],
    date_mention_id: Optional[str],
    date_val: Optional[str],
    local_copy: Optional[str],
    book: str,
) -> Dict[str, Any]:
    """Create spec-compliant image record."""
    url = img.get("url")
    alt = img.get("alt_text", "")
    content_type = _classify_content_type(alt, url or "")

    record: Dict[str, Any] = {
        "ImageID": image_id,
        "image_title": alt or "Untitled",
        "image_type": "source_material",
        "content_type": content_type,
        "source": f"Original book illustration - {book}",
        "place_name": place_name,
        "PlaceMentionID": place_mention_id,
        "date": date_val,
        "DateMentionID": date_mention_id,
        "resource_type": "online" if url else "offline",
        "url": url,
        "local_copy": local_copy,
        "url_capture_date": None,
        "license": "Public Domain - Original source material",
        "description": alt,
        "extracted_date": datetime.utcnow().isoformat() + "Z",
    }

    if event_ctx:
        record.update(event_ctx)
    else:
        record["EventID"] = None
        record["Event_Name"] = None
        record["Sub-eventID"] = None
        record["Sub-event_Name"] = None

    return record


def _load_event_data(parsed_file: Path) -> Optional[dict]:
    """Load corresponding event file for a parsed file."""
    event_file = parsed_file.parent / parsed_file.name.replace(
        "-parsed.json", "-event.json"
    )
    if not event_file.exists():
        return None
    try:
        return json.loads(event_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _process_single_image(
    img: dict,
    event_data: Optional[dict],
    book: str,
    places_dir: Path,
    dates_dir: Path,
    download: bool,
    storage_dir: Path,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Process one image. Returns (record, local_copy)."""
    image_id = str(ulid_mod.new())

    event_ctx = None
    if event_data:
        event_ctx = _find_sub_event_for_image(img, event_data)

    place_mention_id = place_name = date_mention_id = date_val = None
    if event_ctx and event_ctx.get("Sub-eventID"):
        seid = event_ctx["Sub-eventID"]
        place_mention_id, place_name = _find_linked_place(seid, places_dir)
        date_mention_id, date_val = _find_linked_date(seid, dates_dir)

    local_copy = None
    url = img.get("url", "")
    if download and url:
        local_copy, _ = _download_image(url, image_id, storage_dir)

    record = _create_image_record(
        img,
        image_id,
        event_ctx,
        place_mention_id,
        place_name,
        date_mention_id,
        date_val,
        local_copy,
        book,
    )
    return record, local_copy


def _load_existing_urls(images_dir: Path, index: dict) -> set:
    """Build set of already-indexed URLs to skip duplicates."""
    urls: set = set()
    for fname in index.values():
        fpath = images_dir / fname
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text())
                if data.get("url"):
                    urls.add(data["url"])
            except (json.JSONDecodeError, OSError):
                pass
    return urls


def extract_images(
    output_dir: Path,
    places_dir: Path,
    dates_dir: Path,
    download: bool = True,
    image_storage_path: Optional[Path] = None,
) -> int:
    """Extract images from parsed files, link to events, save as entities.

    Returns: number of images extracted.
    """
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = image_storage_path or Path("cache/images")

    index_path = images_dir / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}

    existing_urls = _load_existing_urls(images_dir, index)

    extracted = 0
    downloaded = 0

    for parsed_file in sorted(output_dir.rglob("*-parsed.json")):
        parsed = json.loads(parsed_file.read_text())
        imgs = parsed.get("images", [])
        if not imgs:
            continue

        book = parsed.get("book", "")
        event_data = _load_event_data(parsed_file)

        for img in imgs:
            url = img.get("url", "")
            alt = img.get("alt_text", "")

            if url and url in existing_urls:
                continue
            if _classify_content_type(alt, url) == "map":
                continue

            record, local_copy = _process_single_image(
                img, event_data, book, places_dir, dates_dir, download, storage_dir
            )

            out_file = images_dir / f"{record['ImageID']}.json"
            with open(out_file, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2, ensure_ascii=False)

            index[record["ImageID"]] = out_file.name
            if url:
                existing_urls.add(url)
            extracted += 1
            if local_copy:
                downloaded += 1

            logger.info(
                "  ✓ %s — %s",
                alt[:50] or "Untitled",
                "linked" if record.get("EventID") else "unlinked",
            )

    # Save index
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    logger.info(
        "Image extraction complete: %d extracted, %d downloaded", extracted, downloaded
    )
    return extracted
