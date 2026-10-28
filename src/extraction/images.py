"""Image extraction from source material.

Extracts images parsed in Phase 1, creates spec-compliant image entities
with event/place/date linking, and optionally downloads image files.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import ulid as ulid_mod

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 30
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25MB cap per image


def fetch_image_bytes(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """SSRF-safe fetch of an image URL. Returns (bytes, ext) or (None, None).

    Image URLs originate from UNTRUSTED source HTML (ibiblio), so this must NOT be a bare
    requests.get: (1) clean_url enforces http(s) + rejects loopback/private/link-local/cloud-
    metadata hosts; (2) resolved_host_is_safe re-checks every RESOLVED IP (DNS-rebinding);
    (3) allow_redirects=False — a redirect can send a clean host to an internal address
    (169.254.169.254), so redirects are refused rather than blindly followed; (4) the body is
    byte-capped and must be image/* content.
    """
    from src.utils.url_hygiene import clean_url

    body, ext, _reason = fetch_image_bytes_with_reason(url)
    return body, ext


def fetch_image_bytes_with_reason(url: str):
    """Like ``fetch_image_bytes`` but ALSO returns a specific ``reason`` string on failure so the
    caller's OBSERV line can distinguish a real SSRF block from a benign not-an-image rejection
    (the single ``ssrf_or_invalid`` reason previously conflated: ssrf_blocked / not_an_image /
    fetch_error / too_large). Returns (bytes, ext, "ok") on success, (None, None, <reason>) on
    failure."""
    from src.utils.url_hygiene import clean_url

    cleaned = clean_url(url)
    if not cleaned:
        logger.debug("image fetch blocked (scheme/host): %s", url)
        return None, None, "ssrf_blocked"
    resp, resp_reason = _safe_image_response_with_reason(cleaned)
    if resp is None:
        return None, None, resp_reason
    try:
        content_type = resp.headers.get("content-type", "").lower()
        body = b""
        for chunk in resp.iter_content(8192):
            body += chunk
            if len(body) > _MAX_IMAGE_BYTES:
                logger.warning(
                    "image exceeded %d bytes, skipping: %s", _MAX_IMAGE_BYTES, cleaned
                )
                return None, None, "too_large"
        if "image" not in content_type and len(body) < 1000:
            return None, None, "not_an_image"
        if not _is_valid_image_body(body, content_type):
            logger.debug(
                "rejected non-image content (ct=%s): %s", content_type, cleaned
            )
            return None, None, "not_an_image"
        return body, _ext_for(content_type, cleaned), "ok"
    except Exception as exc:  # noqa: BLE001 - fetch failure must be non-fatal
        logger.debug("Failed to download %s: %s", cleaned, exc)
        return None, None, "fetch_error"


def _safe_image_response(cleaned: str):
    """SSRF-safe GET of an already-clean_url'd image URL: resolve+vet the host (DNS-rebinding) and
    PIN the connection to a vetted IP (keeps Host + TLS SNI = host, allow_redirects=False). Returns
    a streamed 200 ``requests.Response``, or None if blocked/non-200/failed."""
    resp, _reason = _safe_image_response_with_reason(cleaned)
    return resp


def _safe_image_response_with_reason(cleaned: str):
    """Reason-aware variant of ``_safe_image_response``. Returns (response, "ok") or
    (None, <ssrf_blocked|fetch_error|bad_status>)."""
    from urllib.parse import urlparse

    from src.utils.url_hygiene import resolved_host_is_safe, _pinned_get

    host = (urlparse(cleaned).hostname or "").lower()
    vetted = resolved_host_is_safe(host)
    if vetted is None:
        logger.warning("image fetch blocked (host resolves non-public): %s", host)
        return None, "ssrf_blocked"
    try:
        resp = _pinned_get(
            cleaned,
            host,
            vetted,
            timeout=_DOWNLOAD_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
    except Exception as exc:  # noqa: BLE001 - fetch failure must be non-fatal
        logger.debug("Failed to download %s: %s", cleaned, exc)
        return None, "fetch_error"
    if resp.status_code != 200:
        return None, "bad_status"
    return resp, "ok"


def _is_valid_image_body(body: bytes, content_type: str) -> bool:
    """Accept only a real image: trust an image/* content-type, else require a known image
    magic-byte signature — blocks a non-image body (HTML error page, PDF, zip) from being saved
    under an image extension."""
    _MAGIC = (
        b"\xff\xd8\xff",  # jpeg
        b"\x89PNG\r\n\x1a\n",  # png
        b"GIF87a",
        b"GIF89a",  # gif
        b"II*\x00",
        b"MM\x00*",  # tiff
        b"RIFF",  # webp (RIFF....WEBP)
    )
    return content_type.startswith("image/") or any(
        body.startswith(sig) for sig in _MAGIC
    )


def _ext_for(content_type: str, cleaned: str) -> str:
    """Pick the file extension from content-type (preferred) or the URL suffix."""
    low = cleaned.lower()
    if "png" in content_type or low.endswith(".png"):
        return "png"
    if "tif" in content_type or low.endswith((".tif", ".tiff")):
        return "tif"
    if "gif" in content_type or low.endswith(".gif"):
        return "gif"
    return "jpg"


def _classify_content_type(alt_text: str, url: str) -> str:
    """Classify image content type from alt text and URL."""
    text = f"{alt_text} {url}".lower()
    if any(w in text for w in ("map ", "map_", "/maps/")):
        return "map"
    if any(w in text for w in ("diagram", "chart", "plan ", "sketch")):
        return "diagram"
    return "photograph"


def _image_matches_sub_event(se: dict, url: str, resource_id: str, alt: str) -> bool:
    """True if the image (by url / resource_id / sufficiently-long alt text) appears in the
    sub-event's serialized fulltext."""
    fulltext = json.dumps(se.get("Sub-event_fulltext", {}))
    return bool(
        (url and url in fulltext)
        or (resource_id and resource_id in fulltext)
        or (alt and len(alt) > 10 and alt in fulltext)
    )


def _find_sub_event_for_image(img: dict, event_data: dict) -> Optional[Dict[str, str]]:
    """Find which sub-event contains this image. Returns event context dict."""
    url = img.get("url", "")
    alt = img.get("alt_text", "")
    resource_id = img.get("resource_id", "")

    event = event_data.get("Event", {})
    event_id = event.get("EventID") or None
    event_name = event_data.get("Chapter", "")

    for se in event.get("Sub-events", []):
        if _image_matches_sub_event(se, url, resource_id, alt):
            return {
                "EventID": event_id,
                "Event_Name": event_name,
                "Sub-eventID": se.get("Sub-eventID") or None,
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
                return data.get("PlaceID"), data.get("current_name")
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
                date_val = data.get("date_start") or data.get("date")
                return data.get("DateID"), date_val
    return None, None


def _relativize(filepath: Path) -> str:
    """Return ``filepath`` relative to its ``output/`` ancestor (e.g. 'images/files/X.jpg'),
    so stored local_copy paths are portable, never absolute '/app/...'. Falls back to the
    last 3 path components if there is no 'output' ancestor."""
    parts = filepath.parts
    if "output" in parts:
        i = len(parts) - 1 - parts[::-1].index("output")
        return str(Path(*parts[i + 1 :]))
    return str(Path(*parts[-3:])) if len(parts) >= 3 else filepath.name


def _ext_to_mime(ext: Optional[str]) -> Optional[str]:
    """Map a file extension to an image MIME type (schema.org encodingFormat). None if unknown."""
    if not ext:
        return None
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "tif": "image/tiff",
        "tiff": "image/tiff",
        "bmp": "image/bmp",
    }.get(ext.lower().lstrip("."))


def _image_dimensions(body: bytes) -> Tuple[Optional[int], Optional[int]]:
    """(width, height) in px from image bytes via PIL, or (None, None). Fail-safe + decompression-
    bomb guarded (never raises, never crashes the image pass)."""
    try:
        import io

        from PIL import Image as _PILImage

        with _PILImage.open(io.BytesIO(body)) as im:
            w, h = im.size
        return int(w), int(h)
    except Exception as exc:  # noqa: BLE001 - dimensions are best-effort metadata
        logger.debug("image dimensions unavailable: %s", exc)
        return None, None


def _download_image(
    url: str, image_id: str, storage_dir: Path
) -> Tuple[Optional[str], Optional[str], Optional[int], Optional[int], Optional[str]]:
    """Download image (SSRF-safe). Returns (relative_local_path, file_format, width, height,
    encodingFormat) or (None,)*5 on failure. Media metadata (dims + MIME) is derived from the
    SAME bytes we download — deterministic, no extra fetch. Fail-safe.

    local_copy is stored RELATIVE (to the ``output/`` root, e.g. ``images/files/<id>.jpg``) so
    records are portable across containers/hosts (never an absolute ``/app/...`` path).
    """
    body, ext = fetch_image_bytes(url)
    if body is None:
        return None, None, None, None, None
    width, height = _image_dimensions(body)
    mime = _ext_to_mime(ext)
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        filepath = storage_dir / f"{image_id}.{ext}"
        filepath.write_bytes(body)
        return _relativize(filepath), ext, width, height, mime
    except Exception as exc:  # noqa: BLE001 - write failure must be non-fatal
        logger.debug("Failed to save %s: %s", url, exc)
        return None, None, width, height, mime


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


def _set_media_metadata(
    record: Dict[str, Any],
    image_id: str,
    width: Optional[int],
    height: Optional[int],
    encoding_format: Optional[str],
) -> None:
    """Set schema.org media metadata (width/height/encodingFormat) on an image record when known.
    Null-over-guess; fail-safe (mutates ``record`` in place)."""
    if width is not None and height is not None:
        record["width"] = width
        record["height"] = height
        logger.debug(
            "OBSERV image.media_meta image=%s width=%d height=%d fmt=%s",
            image_id,
            width,
            height,
            encoding_format or "-",
        )
    if encoding_format:
        record["encodingFormat"] = encoding_format


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


def _apply_vision_caption(
    record: Dict[str, Any],
    captioner: Any,
    local_copy: str,
    storage_dir: Path,
    img: dict,
    event_ctx: Optional[dict],
    place_name: Optional[str],
    date_val: Optional[str],
    book: str,
) -> None:
    """VISION step: identify the image (don't OCR it). Only when enabled + we have local bytes
    to look at. Captioner never raises / never fabricates. Mutates ``record`` in place.
    """
    try:
        # local_copy is RELATIVE (stored portably); read the bytes from the actual
        # storage_dir (which _download_image wrote to) rather than the relative string.
        image_bytes = (storage_dir / Path(local_copy).name).read_bytes()
    except OSError:
        image_bytes = None
    caption = captioner.caption(
        image_bytes,
        {
            "alt_text": img.get("alt_text", ""),
            "sub_event_name": (event_ctx or {}).get("Sub-event_Name", ""),
            "place_name": place_name or "",
            "date": date_val or "",
            "book": book,
            "classification": record.get("content_type", "unknown"),
        },
    )
    cap = caption.to_dict()
    if cap["description"]:
        record["description"] = cap["description"]
    record["caption_confidence"] = cap["caption_confidence"]
    record["caption_method"] = cap["caption_method"]
    record["needs_review"] = cap["needs_review"]


def _process_single_image(
    img: dict,
    event_data: Optional[dict],
    book: str,
    places_dir: Path,
    dates_dir: Path,
    download: bool,
    storage_dir: Path,
    captioner: Optional[Any] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Process one image. Returns (record, local_copy).

    When ``captioner`` is provided and the image was downloaded locally, run a
    Grok-vision captioning pass (identify the photo/diagram using the linked
    surrounding context) and populate ``description`` + caption fields. The
    deterministic ``content_type`` is the prior; vision fills the semantic
    residual, low-confidence results are flagged needs_review, never fabricated.
    """
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
    width = height = None
    encoding_format = None
    url = img.get("url", "")
    if download and url:
        local_copy, _, width, height, encoding_format = _download_image(
            url, image_id, storage_dir
        )

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
    # Tier-1 deterministic media metadata (schema.org images 2.27) — derived from the bytes we
    # already downloaded; only set when known (null-over-guess). Fail-safe.
    _set_media_metadata(record, image_id, width, height, encoding_format)

    # VISION step: identify the image (don't OCR it). Only when enabled + we have
    # local bytes to look at. Captioner never raises / never fabricates.
    if captioner is not None and local_copy:
        _apply_vision_caption(
            record,
            captioner,
            local_copy,
            storage_dir,
            img,
            event_ctx,
            place_name,
            date_val,
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
    captioner: Optional[Any] = None,
) -> int:
    """Extract images from parsed files, link to events, save as entities.

    When ``captioner`` is None, it is built from config: a Grok-vision
    ``ImageCaptioner`` if ``images.vision_caption`` is enabled, else no captioning
    (description stays the alt text — prior behavior). Returns: number extracted.
    """
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    # Default UNDER output/ (synced to S3). The old cache/images default was ephemeral —
    # downloaded bytes would die with the container if a config omitted the path.
    storage_dir = image_storage_path or (output_dir / "images" / "files")

    if captioner is None:
        try:
            from src.utils.config import load_config

            if load_config().get("images", {}).get("vision_caption", False):
                from src.extraction.image_captioner import ImageCaptioner

                captioner = ImageCaptioner()
                logger.info("Image vision captioning enabled")
        except Exception as e:  # noqa: BLE001 - captioning is optional
            logger.warning("Could not init image captioner (skipping): %s", e)

    index_path = images_dir / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}

    existing_urls = _load_existing_urls(images_dir, index)

    extracted = 0
    downloaded = 0
    files_scanned = 0
    images_seen = 0
    skipped = 0

    for parsed_file in sorted(output_dir.rglob("*-parsed.json")):
        parsed = json.loads(parsed_file.read_text())
        imgs = parsed.get("images", [])
        if not imgs:
            continue
        files_scanned += 1

        book = parsed.get("book", "")
        event_data = _load_event_data(parsed_file)

        for img in imgs:
            images_seen += 1
            e_d, d_d, s_d = _extract_one_image(
                img,
                event_data,
                book,
                places_dir,
                dates_dir,
                download,
                storage_dir,
                captioner,
                images_dir,
                index,
                existing_urls,
            )
            extracted += e_d
            downloaded += d_d
            skipped += s_d

    # Save index
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2, ensure_ascii=False)

    logger.info(
        "Image extraction complete: %d extracted, %d downloaded", extracted, downloaded
    )
    logger.info(
        "OBSERV images.extract files_scanned=%d images_seen=%d extracted=%d "
        "downloaded=%d skipped=%d",
        files_scanned,
        images_seen,
        extracted,
        downloaded,
        skipped,
    )
    return extracted


def _extract_one_image(
    img,
    event_data,
    book,
    places_dir,
    dates_dir,
    download,
    storage_dir,
    captioner,
    images_dir,
    index,
    existing_urls,
) -> "Tuple[int, int, int]":
    """Process one parsed image: skip if already-seen or a map, else build+write the entity, update
    the index/existing_urls (by reference), and download the file. Returns (extracted, downloaded,
    skipped) deltas (each 0 or 1)."""
    url = img.get("url", "")
    alt = img.get("alt_text", "")
    if url and url in existing_urls:
        return 0, 0, 1
    if _classify_content_type(alt, url) == "map":
        return 0, 0, 1

    record, local_copy = _process_single_image(
        img, event_data, book, places_dir, dates_dir, download, storage_dir, captioner
    )
    out_file = images_dir / f"{record['ImageID']}.json"
    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(out_file, record)
    index[record["ImageID"]] = out_file.name
    if url:
        existing_urls.add(url)
    logger.info(
        "  ✓ %s — %s",
        alt[:50] or "Untitled",
        "linked" if record.get("EventID") else "unlinked",
    )
    return 1, (1 if local_copy else 0), 0
