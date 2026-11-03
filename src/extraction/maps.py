"""Maps extraction from source material."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional


from src.extraction.map_common import base_map_record

logger = logging.getLogger(__name__)


def _check_existing_download(output_path: Path) -> Optional[str]:
    """Check if map is already downloaded with any extension."""
    for ext in ["jpg", "jpeg", "png", "gif", "tif", "tiff", "pdf"]:
        existing = output_path.with_suffix(f".{ext}")
        if existing.exists():
            logger.debug("    Already downloaded: %s", existing.name)
            return str(existing.relative_to(existing.parents[2]))
    return None


def _determine_map_extension(response, url: str) -> str:
    """Determine the canonical file extension from content-type (preferred) or the URL suffix.
    Normalizes to single canonical forms (jpeg->jpg, tiff->tif). Covers jpg/png/gif/tif/pdf; TIFF
    and GIF matter for non-ibiblio (NARA/archival) map sources."""
    content_type = (response.headers.get("content-type", "") or "").lower()
    # content-type substring -> canonical ext (order: most specific first)
    ct_map = (
        ("jpeg", "jpg"),
        ("jpg", "jpg"),
        ("png", "png"),
        ("tiff", "tif"),
        ("tif", "tif"),
        ("gif", "gif"),
        ("pdf", "pdf"),
    )
    for needle, ext in ct_map:
        if needle in content_type:
            return ext
    # Fallback to URL suffix, normalized to the canonical set.
    raw = url.rsplit(".", maxsplit=1)[-1].lower()
    norm = {"jpeg": "jpg", "tiff": "tif"}.get(raw, raw)
    return norm if norm in ("jpg", "png", "gif", "tif", "pdf") else "jpg"


def _download_map_image(
    url: str, output_path: Path, timeout: int = 30
) -> Optional[str]:
    """Download map image from URL (SSRF-safe). ``timeout`` is retained for signature
    compatibility but ignored — fetch_image_bytes owns its own download timeout."""
    from src.extraction.images import fetch_image_bytes

    # Check if already downloaded
    existing = _check_existing_download(output_path)
    if existing:
        return existing

    body, ext = fetch_image_bytes(
        url
    )  # SSRF-safe (clean_url + resolved IP + no redirect)
    if body is None:
        return None
    try:
        output_file = output_path.with_suffix(f".{ext}")
        with open(output_file, "wb") as f:
            f.write(body)
        logger.debug("    Downloaded: %s", output_file.name)
        return str(output_file.relative_to(output_file.parents[2]))
    except Exception as e:  # noqa: BLE001 - write failure must be non-fatal
        logger.warning("    Failed to save %s: %s", url, e)
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

    # Common fields from the shared skeleton; merge the source-DOCUMENT-specific fields on top.
    return {
        **base_map_record(
            map_id=map_ulid,
            map_title=description,
            description=description,
            map_type=map_type,
            source_url=url if url else None,
            local_path=local_path,
            local_image_path=local_image_path,
            file_format=file_format,
            storage_backend=storage_backend,
            event_id=event_id,
            event_name=event_name,
            sub_event_id=sub_event_id,
            sub_event_name=sub_event_name,
            place_name=place_name,
            place_mention_id=place_id,
            date=date_str,
            date_mention_id=date_id,
        ),
        "source_book": book,
        "source_author": author,
        "source_series": series if series else None,
        "page_number": None,
        "figure_number": map_id if map_id else None,
    }
