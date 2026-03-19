"""Military equipment extraction from event data."""

import json
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ulid
from pydantic import BaseModel, Field, field_validator

from src.grok_client import GrokClient
from src.utils.http_pool import get_session
from src.utils.json_validator import _fix_invalid_ulids

logger = logging.getLogger(__name__)


# Pydantic models for structured extraction
class UsingUnit(BaseModel):
    """Unit using equipment."""

    PeopleGroupID: str = Field(description="26-character ULID")
    name: str = Field(description="Unit name")


class UsingPerson(BaseModel):
    """Person using equipment."""

    PersonID: str = Field(description="26-character ULID")
    name: str = Field(description="Person name")


class SupportingUnit(BaseModel):
    """Supporting unit with equipment."""

    support_type: str = Field(
        description="Type: armor, naval, aircraft, artillery, etc."
    )
    PeopleGroupID: Optional[str] = Field(default=None, description="26-character ULID")
    unit_name: Optional[str] = Field(default=None, description="Unit name")
    EquipmentID: Optional[str] = Field(default=None, description="Equipment ULID")
    equipment_name: Optional[str] = Field(default=None, description="Equipment name")


class PerformanceNotes(BaseModel):
    """Performance observations."""

    successes: List[str] = Field(default_factory=list)
    failures: List[str] = Field(default_factory=list)
    field_modifications: List[str] = Field(default_factory=list)


class MediaItem(BaseModel):
    """Media item (photo, video, audio, document)."""

    media_type: str = Field(description="photo, video, audio, document")
    url: str = Field(description="URL to media")
    title: Optional[str] = Field(default=None, description="Media title/caption")
    source: str = Field(description="wikipedia, commons, archive, etc.")
    license: Optional[str] = Field(default=None, description="License info")
    description: Optional[str] = Field(default=None, description="Media description")
    maintenance_issues: List[str] = Field(default_factory=list)


class EquipmentMention(BaseModel):
    """Equipment mention in event."""

    MentionID: str = Field(description="26-character ULID")
    book: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    chapter: Optional[str] = None
    paragraph_numbers: List[int] = Field(default_factory=list)
    variant_mentioned: Optional[str] = None
    context: Optional[str] = None
    original_text: Optional[str] = None
    EventID: str = Field(description="Links to Event.EventID")
    Event_Name: Optional[str] = None
    Sub_eventID: str = Field(description="Links to Sub-eventID in Event.Sub-events[]")
    Sub_event_Name: Optional[str] = None
    date: Optional[str] = None
    DateID: Optional[str] = Field(default=None, description="Links to date file")
    DateMentionID: Optional[str] = Field(
        default=None, description="Links to mention in date file"
    )
    using_unit: Optional[UsingUnit] = None
    using_person: Optional[UsingPerson] = None
    supporting_units: List[SupportingUnit] = Field(default_factory=list)
    performance_notes: Optional[PerformanceNotes] = None
    media: List[MediaItem] = Field(
        default_factory=list, description="Photos, videos, documents"
    )


class Variant(BaseModel):
    """Equipment variant."""

    variant_name: str
    differences: Optional[str] = None
    alternate_names: List[str] = Field(default_factory=list)


class EquipmentExtraction(BaseModel):
    """LLM extraction output."""

    common_name: str = Field(description="Common name (e.g., 'Sherman', 'Tiger')")
    technical_identifier: Optional[str] = Field(
        default=None,
        description="Official designation (e.g., 'M4', 'Panzerkampfwagen VI')",
    )
    description: Optional[str] = Field(default=None, description="General description")
    alternate_names: List[str] = Field(default_factory=list)
    category: str = Field(
        description="armor, aircraft, naval, artillery, infantry_weapons, etc."
    )
    subcategory: Optional[str] = Field(
        default=None, description="e.g., medium_tank, fighter, destroyer"
    )
    country_of_origin: Optional[str] = Field(
        default=None,
        description="ISO 3166-1 alpha-3 country code (e.g., 'USA', 'DEU', 'GBR', 'FRA', 'ITA', 'JPN', 'CAN')",
    )
    variants: List[Variant] = Field(default_factory=list)
    specifications: Optional[Dict[str, Any]] = Field(
        default=None, description="Technical specs"
    )
    using_unit_name: Optional[str] = Field(
        default=None, description="Unit using equipment"
    )
    using_person_name: Optional[str] = Field(
        default=None, description="Person using equipment"
    )
    performance_successes: List[str] = Field(default_factory=list)
    performance_failures: List[str] = Field(default_factory=list)
    field_modifications: List[str] = Field(default_factory=list)
    maintenance_issues: List[str] = Field(default_factory=list)
    variant_mentioned: Optional[str] = Field(
        default=None, description="Which variant in this event"
    )
    context: Optional[str] = Field(default=None, description="Brief situation summary")
    original_text: Optional[str] = Field(
        default=None, description="Text mentioning equipment"
    )
    paragraph_numbers: List[int] = Field(
        default_factory=list, description="Paragraph numbers where mentioned"
    )
    supporting_unit_names: List[str] = Field(
        default_factory=list,
        description="Names of supporting units (e.g., air support, artillery)",
    )

    @field_validator("specifications", mode="before")
    @classmethod
    def validate_specifications(cls, v):  # pylint: disable=unused-argument
        """Convert string specifications to None."""
        _ = cls  # Used by decorator
        if isinstance(v, str):
            return None
        return v

    @field_validator("variants", mode="before")
    @classmethod
    def validate_variants(cls, v):  # pylint: disable=unused-argument
        """Convert string variants to empty list."""
        _ = cls  # Used by decorator
        if isinstance(v, str):
            return []
        if isinstance(v, list):
            # Filter out any string items
            return [item for item in v if isinstance(item, dict)]
        return v


def _load_json_files(
    directory: Path, skip_files: List[str]
) -> List[tuple[Path, Dict[str, Any]]]:
    """Load all JSON files from directory, skipping specified files."""
    results: List[tuple[Path, Dict[str, Any]]] = []
    if not directory.exists():
        return results

    for json_file in directory.glob("*.json"):
        if json_file.name in skip_files:
            continue
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)
                results.append((json_file, data))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load %s: %s", json_file.name, e)
        except Exception as e:
            logger.debug("Skipping %s: %s", json_file.name, e)

    return results


def _build_people_index(output_root: Path) -> Dict[str, str]:
    """Build people name -> PersonID index."""
    people_dir = output_root / "people"
    skip_files = [
        "index.json",
        "duplicate_report.json",
        "not_duplicates.json",
        ".processed_events.json",
    ]

    index = {}
    for _, person_data in _load_json_files(people_dir, skip_files):
        if "PersonID" in person_data and "name" in person_data:
            index[person_data["name"]] = person_data["PersonID"]

    return index


def _build_groups_index(output_root: Path) -> Dict[str, str]:
    """Build group name -> GroupID index (includes aliases)."""
    groups_dir = output_root / "people_groups"
    skip_files = ["index.json", ".processed_events.json", "related_groups_report.json"]

    index = {}
    for _, group_data in _load_json_files(groups_dir, skip_files):
        group_id = group_data.get("GroupID") or group_data.get("PeopleGroupID")
        group_name = group_data.get("group_name") or group_data.get("name")

        if group_id and group_name:
            index[group_name] = group_id
            # Also index aliases
            for alias in group_data.get("aliases", []):
                index[alias] = group_id

    return index


def _build_dates_index(output_root: Path) -> Dict[str, Dict[str, str]]:
    """Build (EventID:Sub_eventID) -> DateID index."""
    dates_dir = output_root / "dates"
    skip_files = ["index.json"]

    index = {}
    for _, date_data in _load_json_files(dates_dir, skip_files):
        if "DateID" not in date_data:
            continue

        # Index by EventID + Sub_eventID for lookup
        for mention in date_data.get("event_mentions", []):
            if "EventID" in mention and "Sub_eventID" in mention:
                key = f"{mention['EventID']}:{mention['Sub_eventID']}"
                index[key] = {
                    "DateID": date_data["DateID"],
                    "DateMentionID": mention.get("MentionID"),
                }

    return index


def load_entity_indices(output_root: Path) -> tuple[dict, dict, dict]:
    """Load entity indices from output directory.

    Returns:
        Tuple of (people_index, people_groups_index, dates_index)

    Raises:
        FileNotFoundError: If output_root doesn't exist
    """
    if not output_root.exists():
        raise FileNotFoundError(f"Output root directory not found: {output_root}")

    people_index = _build_people_index(output_root)
    people_groups_index = _build_groups_index(output_root)
    dates_index = _build_dates_index(output_root)

    logger.info(
        "Loaded %s people, %s groups, %s date mentions",
        len(people_index),
        len(people_groups_index),
        len(dates_index),
    )
    return people_index, people_groups_index, dates_index


def load_equipment_index(equipment_dir: Path) -> Dict[str, Path]:
    """Load equipment index mapping name to file path.

    Returns:
        Dict mapping common_name to file path
    """
    index: Dict[str, Path] = {}
    if not equipment_dir.exists():
        return index

    for eq_file in equipment_dir.glob("*.json"):
        if eq_file.name == "index.json":
            continue
        try:
            with open(eq_file, encoding="utf-8") as file_handle:
                eq_data = json.load(file_handle)
                if "common_name" in eq_data:
                    index[eq_data["common_name"]] = eq_file
        except Exception as e:
            logger.warning("Failed to load equipment file %s: %s", eq_file.name, e)

    return index


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


def _extract_year_from_date(
    sub_event_id: Optional[str], dates_index: Optional[Dict[str, Dict[str, str]]]
) -> Optional[str]:
    """Extract year from date index."""
    if not sub_event_id or not dates_index or sub_event_id not in dates_index:
        return None

    date_info = dates_index[sub_event_id]
    date_str = date_info.get("date_start", "")

    if date_str and len(date_str) >= 4:
        return date_str[:4]  # Extract year (YYYY)

    return None


def _merge_enriched_data(
    equipment_data: Dict[str, Any], enriched: Dict[str, Any]
) -> None:
    """Merge enriched data into equipment data (don't overwrite existing)."""
    for key in ["description", "specifications", "alternate_names", "variants"]:
        if key in enriched and enriched[key]:
            # Only use enriched data if field is missing or empty
            if key not in equipment_data or not equipment_data[key]:
                equipment_data[key] = enriched[key]
                logger.debug("  Enriched %s: %s", key, type(enriched[key]).__name__)


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
            pass  # Ignore cleanup errors


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
    import subprocess

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
    """Extract media URLs from Wikipedia/Grokipedia.

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client

    Returns:
        List of media items with URLs
    """
    identifier = technical_identifier or common_name

    prompt = f"""Find media (photos, videos, documents) for this WWII military equipment: {identifier} ({common_name})
Category: {category}

Search Wikipedia and Wikimedia Commons for:
1. Historical photos of the equipment
2. Technical diagrams or schematics
3. Period videos or newsreels (if available)
4. Official documents or manuals

For each media item, provide:
- media_type: "photo", "video", "audio", or "document"
- url: Direct URL to the media file
- title: Brief title or caption
- source: "wikipedia", "commons", "archive", etc.
- license: License information (e.g., "Public Domain", "CC BY-SA")
- description: Brief description

Return as JSON array (limit to 5 most relevant items):
[
  {{
    "media_type": "photo",
    "url": "https://commons.wikimedia.org/...",
    "title": "M4 Sherman in Normandy",
    "source": "commons",
    "license": "Public Domain",
    "description": "Sherman tank during Operation Overlord"
  }}
]

If no media found, return empty array []."""

    try:
        response = grok_client.chat_completion(
            prompt,
            temperature=0.1,
            use_cache=True,
            cache_type="equipment_media",
        )

        media_list = json.loads(response)
        if isinstance(media_list, dict) and "media" in media_list:
            media_list = media_list["media"]

        logger.debug("Found %s media items for %s", len(media_list), common_name)
        return media_list if isinstance(media_list, list) else []
    except Exception as e:
        logger.warning("Failed to extract media for %s: %s", common_name, e)
        return []


def _enrich_equipment_data(
    common_name: str,
    technical_identifier: Optional[str],
    category: str,
    grok_client: GrokClient,
) -> Dict[str, Any]:
    """Enrich equipment data with external sources (Wikipedia/Grokipedia).

    Args:
        common_name: Equipment common name
        technical_identifier: Technical designation
        category: Equipment category
        grok_client: Grok API client

    Returns:
        Dict with enriched data (description, specifications, etc.)
    """
    identifier = technical_identifier or common_name

    prompt = f"""Look up information about this WWII military equipment: {identifier} ({common_name})
Category: {category}

Provide a brief summary with:
1. Description (2-3 sentences)
2. Key specifications (if applicable: weight, dimensions, armament, speed, range, crew)
3. Alternate names/designations
4. Notable variants

Return as JSON:
{{
  "description": "Brief description",
  "specifications": {{"key": "value"}},
  "alternate_names": ["name1", "name2"],
  "variants": [{{"variant_name": "name", "description": "desc"}}]
}}

If information is not available, return empty fields."""

    try:
        response = grok_client.chat_completion(
            prompt,
            temperature=0.1,
            use_cache=True,
            cache_type="equipment_enrichment",
        )

        enriched = json.loads(response)
        logger.debug("Enriched data for %s", common_name)
        return enriched
    except Exception as e:
        logger.warning("Failed to enrich equipment data for %s: %s", common_name, e)
        return {}


def _fuzzy_match_equipment(
    name: str, equipment_index: Dict[str, Path], threshold: float = 0.80
) -> Optional[str]:
    """Find best fuzzy match for equipment name.

    Checks both common_name and alternate_names from equipment files.

    Args:
        name: Equipment name to match
        equipment_index: Index of existing equipment
        threshold: Minimum similarity ratio (0.0-1.0), default 0.80

    Returns:
        Matched equipment name or None
    """
    if not equipment_index:
        return None

    best_match = None
    best_ratio = 0.0

    name_lower = name.lower()

    # Check common names
    for existing_name in equipment_index.keys():
        ratio = SequenceMatcher(None, name_lower, existing_name.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = existing_name

    # Also check alternate names in files
    for existing_name, eq_file in equipment_index.items():
        try:
            with open(eq_file, encoding="utf-8") as f:
                eq_data = json.load(f)
                for alt_name in eq_data.get("alternate_names", []):
                    ratio = SequenceMatcher(None, name_lower, alt_name.lower()).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_match = existing_name
        except Exception:  # nosec B112
            continue  # Skip invalid entries

    if best_ratio >= threshold:
        logger.debug("Fuzzy matched '%s' to '%s' (%.2f)", name, best_match, best_ratio)
        return best_match

    return None


def _find_matching_equipment(
    common_name: str, equipment_index: Dict[str, Path]
) -> Optional[str]:
    """Find matching equipment by exact or fuzzy match."""
    if common_name in equipment_index:
        return common_name
    return _fuzzy_match_equipment(common_name, equipment_index)


def _merge_equipment_fields(existing: dict, equipment_data: dict) -> None:
    """Merge equipment fields from new data into existing."""
    for key in [
        "description",
        "alternate_names",
        "subcategory",
        "variants",
        "specifications",
    ]:
        if key not in equipment_data or not equipment_data[key]:
            continue

        if key == "alternate_names" and key in existing:
            # Merge alternate names
            existing[key] = list(set(existing[key] + equipment_data[key]))
        elif key == "variants" and key in existing:
            # Merge variants by variant_name
            existing_variants = {
                v["variant_name"]: v for v in existing.get("variants", [])
            }
            for new_variant in equipment_data.get("variants", []):
                existing_variants[new_variant["variant_name"]] = new_variant
            existing["variants"] = list(existing_variants.values())
        else:
            existing[key] = equipment_data[key]


def _merge_into_existing(
    eq_file: Path, new_mention: dict, equipment_data: dict, matched_name: str
) -> Path:
    """Merge mention into existing equipment file."""
    logger.debug("Merging mention into existing equipment: %s", matched_name)

    # Load existing
    with open(eq_file, encoding="utf-8") as f:
        existing = json.load(f)

    # Check if mention already exists
    existing_mention_ids = {m["MentionID"] for m in existing.get("mentions", [])}
    if new_mention["MentionID"] in existing_mention_ids:
        logger.debug("Mention %s already exists, skipping", new_mention["MentionID"])
        return eq_file

    # Append mention
    existing["mentions"].append(new_mention)

    # Update optional fields
    _merge_equipment_fields(existing, equipment_data)

    # Save
    with open(eq_file, "w") as f:
        json.dump(existing, f, indent=2)

    return eq_file


def _create_new_equipment(
    equipment_data: dict,
    new_mention: dict,
    equipment_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: Optional[GrokClient],
    enable_enrichment: bool,
    verify_media_with_vision: bool,
    dates_index: Optional[Dict[str, Dict[str, str]]],
) -> Path:
    """Create new equipment file."""
    common_name = equipment_data["common_name"]
    logger.debug("Creating new equipment file: %s", common_name)

    # Enrich with external data if enabled
    if enable_enrichment and grok_client:
        sub_event_id = new_mention.get("Sub_eventID")
        _enrich_and_add_media(
            equipment_data,
            common_name,
            grok_client,
            verify_media_with_vision,
            sub_event_id,
            dates_index,
        )

    equipment_id = str(ulid.new())
    equipment_data["EquipmentID"] = equipment_id
    equipment_data["mentions"] = [new_mention]
    equipment_data["extracted_date"] = datetime.now(timezone.utc).isoformat()

    safe_name = common_name.replace(" ", "_").replace("/", "_")
    eq_file = equipment_dir / f"{safe_name}_{equipment_id[:8]}.json"

    with open(eq_file, "w") as f:
        json.dump(equipment_data, f, indent=2)

    # Update index
    equipment_index[common_name] = eq_file

    return eq_file


def merge_or_create_equipment(
    equipment_data: dict,
    new_mention: dict,
    equipment_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: Optional[GrokClient] = None,
    enable_enrichment: bool = False,
    verify_media_with_vision: bool = True,
    dates_index: Optional[Dict[str, Dict[str, str]]] = None,
) -> Path:
    """Merge mention into existing equipment or create new file.

    Args:
        equipment_data: Equipment data (common_name, category, etc.)
        new_mention: New mention to add
        equipment_dir: Output directory
        equipment_index: Index of existing equipment
        grok_client: Grok API client for enrichment
        enable_enrichment: Whether to enrich new equipment with external data
        verify_media_with_vision: Verify media relevance with Grok vision API
        dates_index: Index of dates by Sub-eventID for temporal filtering

    Returns:
        Path to equipment file
    """
    common_name = equipment_data["common_name"]

    # Find matching equipment
    matched_name = _find_matching_equipment(common_name, equipment_index)

    if matched_name:
        eq_file = equipment_index[matched_name]
        return _merge_into_existing(eq_file, new_mention, equipment_data, matched_name)
    else:
        return _create_new_equipment(
            equipment_data,
            new_mention,
            equipment_dir,
            equipment_index,
            grok_client,
            enable_enrichment,
            verify_media_with_vision,
            dates_index,
        )


def _validate_event_data(event_data: Dict[str, Any], event_file: Path) -> bool:
    """Validate event data structure. Returns True if valid."""
    if "Event" not in event_data:
        logger.error("Missing 'Event' key in %s", event_file)
        return False
    if "EventID" not in event_data["Event"]:
        logger.error("Missing 'EventID' in %s", event_file)
        return False
    return True


def _extract_equipment_with_llm(
    event_data: Dict[str, Any], grok_client: GrokClient, max_retries: int
) -> Optional[List[Dict[str, Any]]]:
    """Extract equipment using LLM with retry logic."""
    prompt = f"""Extract military equipment mentioned in this WWII event data.

IMPORTANT: Always identify specific equipment by name/designation, not generic categories.
For example, use "Browning Automatic Rifle" not "Light machine guns and automatic rifles",
"M4 Sherman" not "Medium tanks", "M1 Garand" not "Rifles". If the text only mentions a
generic category without identifying specific equipment, omit it.

Event Data:
{json.dumps(event_data, indent=2)}

For each piece of equipment mentioned:
1. Identify common name and technical designation
2. Determine category (armor, aircraft, naval, artillery, infantry_weapons, communications, vehicles, uniforms, other)
3. Extract any variants mentioned
4. Note specifications if mentioned (weight, armament, armor, speed, range, crew)
5. Identify which unit or person was using it
6. Identify supporting units (e.g., air support, artillery support, naval support)
7. Extract performance observations (successes, failures, modifications, maintenance issues)
8. Extract context (brief situation summary)
9. Extract original text mentioning the equipment
10. Note which variant was mentioned (if applicable)
11. Extract paragraph numbers where mentioned (if available)

Return a JSON array of equipment objects with these fields:
- common_name (required)
- technical_identifier (optional)
- description (optional, general description)
- alternate_names (optional array)
- category (required)
- subcategory (optional)
- country_of_origin (optional, ISO 3166-1 alpha-3 code e.g. 'USA', 'DEU', 'GBR', 'JPN')
- variants (optional array)
- specifications (optional dict)
- using_unit_name (optional)
- using_person_name (optional)
- supporting_unit_names (optional array, names of supporting units)
- performance_successes (optional array)
- performance_failures (optional array)
- field_modifications (optional array)
- maintenance_issues (optional array)
- variant_mentioned (optional, which variant in this event)
- context (optional, brief situation summary)
- original_text (optional, text mentioning equipment)
- paragraph_numbers (optional array of integers)

Example:
[
  {{
    "common_name": "Sherman",
    "technical_identifier": "M4",
    "category": "armor",
    "subcategory": "medium_tank",
    "using_unit_name": "2nd Armored Division",
    "supporting_unit_names": ["IX Tactical Air Command", "VII Corps Artillery"],
    "variant_mentioned": "M4A1",
    "context": "Attack on St. Lô",
    "original_text": "The Shermans advanced through the hedgerows...",
    "paragraph_numbers": [145, 146],
    "performance_successes": ["Effective against infantry"],
    "performance_failures": ["Outgunned by Panthers"]
  }}
]"""

    for attempt in range(max_retries):
        try:
            equipment_list = grok_client.extract_json(
                prompt,
                temperature=0.1,
                use_cache=(attempt == 0),
                cache_type="equipment",
            )

            if isinstance(equipment_list, dict) and "equipment" in equipment_list:
                equipment_list = equipment_list["equipment"]

            if isinstance(equipment_list, list):
                equipment_list = _fix_invalid_ulids(equipment_list)

            return equipment_list

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning("  ⚠ Attempt %s failed: %s", attempt + 1, e)
                logger.info("  Retrying (%s/%s)...", attempt + 2, max_retries)
            else:
                logger.error("  ✗ All %s attempts failed: %s", max_retries, e)

    return None


def _build_mention(
    eq: EquipmentExtraction,
    event_data: Dict[str, Any],
    using_unit: Optional[Dict[str, str]],
    using_person: Optional[Dict[str, str]],
    performance_notes: Optional[Dict[str, List[str]]],
    supporting_units: List[Dict[str, Any]],
    dates_index: Dict[str, Dict[str, str]],
    output_root: Path,
) -> Dict[str, Any]:
    """Build equipment mention with all metadata."""
    mention_id = str(ulid.new())
    mention = {
        "MentionID": mention_id,
        "EventID": event_data["Event"]["EventID"],
        "Sub_eventID": (
            event_data["Event"]["Sub-events"][0]["Sub-eventID"]
            if event_data["Event"].get("Sub-events")
            else str(ulid.new())
        ),
    }

    # Add metadata and event names
    _add_metadata_to_mention(mention, event_data)
    _add_event_names_to_mention(mention, event_data)

    # Add equipment-specific fields
    if eq.paragraph_numbers:
        mention["paragraph_numbers"] = eq.paragraph_numbers
    if eq.variant_mentioned:
        mention["variant_mentioned"] = eq.variant_mentioned
    if eq.context:
        mention["context"] = eq.context
    if eq.original_text:
        mention["original_text"] = eq.original_text

    # Link to date
    _link_date_to_mention(mention, dates_index, output_root)

    # Add linked entities
    if using_unit:
        mention["using_unit"] = using_unit
    if using_person:
        mention["using_person"] = using_person
    if supporting_units:
        mention["supporting_units"] = supporting_units
    if performance_notes:
        mention["performance_notes"] = performance_notes

    return mention


def _build_equipment_data(eq: EquipmentExtraction) -> Dict[str, Any]:
    """Build equipment data dict from extraction."""
    equipment_data = {
        "common_name": eq.common_name,
        "technical_identifier": eq.technical_identifier or eq.common_name,
        "category": eq.category,
    }

    # Add optional fields
    if eq.description:
        equipment_data["description"] = eq.description
    if eq.alternate_names:
        equipment_data["alternate_names"] = list(eq.alternate_names)  # type: ignore[assignment]
    if eq.subcategory:
        equipment_data["subcategory"] = eq.subcategory
    if eq.country_of_origin:
        equipment_data["country_of_origin"] = eq.country_of_origin
    if eq.variants:
        equipment_data["variants"] = [v.model_dump() for v in eq.variants]  # type: ignore[assignment]
    if eq.specifications:
        equipment_data["specifications"] = dict(eq.specifications)  # type: ignore[assignment]

    return equipment_data


def _process_equipment_item(
    eq_data: Dict[str, Any],
    event_data: Dict[str, Any],
    people_index: Dict[str, str],
    people_groups_index: Dict[str, str],
    dates_index: Dict[str, Dict[str, str]],
    output_root: Path,
    output_dir: Path,
    equipment_index: Dict[str, Path],
    grok_client: GrokClient,
    enable_enrichment: bool = False,
    verify_media_with_vision: bool = True,
) -> Optional[Path]:
    """Process a single equipment item. Returns equipment file path or None."""
    try:
        # Validate and parse
        eq = EquipmentExtraction.model_validate(eq_data)
    except Exception as e:
        logger.warning("  Skipping invalid equipment data: %s", e)
        logger.debug("  Data: %s", eq_data)
        return None

    # Link entities
    using_unit = _link_entity(eq.using_unit_name, people_groups_index, "unit")
    using_person = _link_entity(eq.using_person_name, people_index, "person")
    performance_notes = _build_performance_notes(eq)
    supporting_units = _link_supporting_units(
        eq.supporting_unit_names, people_groups_index, eq.category
    )

    # Build mention and equipment data
    mention = _build_mention(
        eq,
        event_data,
        using_unit,
        using_person,
        performance_notes,
        supporting_units,
        dates_index,
        output_root,
    )
    equipment_data = _build_equipment_data(eq)

    # Merge or create
    try:
        eq_file = merge_or_create_equipment(
            equipment_data,
            mention,
            output_dir,
            equipment_index,
            grok_client,
            enable_enrichment,
            verify_media_with_vision,
            dates_index,
        )
        logger.debug("Updated equipment file: %s", eq_file.name)
        return eq_file
    except Exception as e:
        logger.error("Failed to save equipment '%s': %s", eq.common_name, e)
        return None


def _load_event_data(event_file: Path) -> Optional[Dict[str, Any]]:
    """Load and validate event data from file."""
    try:
        with open(event_file, encoding="utf-8") as f:
            event_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load event file {event_file}: %s", e)
        return None

    if not _validate_event_data(event_data, event_file):
        return None

    return event_data


def _finalize_extraction(
    output_dir: Path,
    modified_files: List[Path],
    event_file: Path,
    processed: Dict[str, bool],
) -> None:
    """Generate index and mark event as processed."""
    if modified_files:
        try:
            generate_equipment_index(output_dir)
        except Exception as e:
            logger.warning("Failed to generate index: %s", e)

    processed[str(event_file)] = True
    _save_processed_registry(output_dir, processed)


def _link_supporting_units(
    supporting_unit_names: List[str],
    people_groups_index: Dict[str, str],
    category: str,
) -> List[Dict[str, Any]]:
    """Link supporting units by name to ID."""
    supporting_units = []
    for unit_name in supporting_unit_names:
        group_id = people_groups_index.get(unit_name)
        support_unit = {
            "support_type": category,  # Use equipment category as support type
            "unit_name": unit_name,
        }
        if group_id:
            support_unit["PeopleGroupID"] = group_id
            logger.debug("Linked supporting unit '%s' to %s", unit_name, group_id)
        else:
            logger.debug("Supporting unit not found: %s", unit_name)
        supporting_units.append(support_unit)
    return supporting_units


def _load_processed_registry(output_dir: Path) -> Dict[str, bool]:
    """Load processed events registry."""
    processed_registry = output_dir / ".processed_events.json"
    if processed_registry.exists():
        with open(processed_registry, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_processed_registry(output_dir: Path, processed: Dict[str, bool]) -> None:
    """Save processed events registry."""
    processed_registry = output_dir / ".processed_events.json"
    with open(processed_registry, "w") as f:
        json.dump(processed, f, indent=2)


def _link_entity(
    entity_name: Optional[str], entity_index: Dict[str, str], entity_type: str
) -> Optional[Dict[str, str]]:
    """Link entity by name to ID."""
    if not entity_name:
        return None
    entity_id = entity_index.get(entity_name)
    if entity_id:
        id_key = "PersonID" if entity_type == "person" else "PeopleGroupID"
        logger.debug("Linked %s '%s' to %s", entity_type, entity_name, entity_id)
        return {id_key: entity_id, "name": entity_name}
    logger.debug("%s not found: %s", entity_type.capitalize(), entity_name)
    return None


def _build_performance_notes(eq: EquipmentExtraction) -> Optional[Dict[str, List[str]]]:
    """Build performance notes from equipment data."""
    if not any(
        [
            eq.performance_successes,
            eq.performance_failures,
            eq.field_modifications,
            eq.maintenance_issues,
        ]
    ):
        return None
    return {
        "successes": eq.performance_successes,
        "failures": eq.performance_failures,
        "field_modifications": eq.field_modifications,
        "maintenance_issues": eq.maintenance_issues,
    }


def _add_metadata_to_mention(
    mention: Dict[str, Any], event_data: Dict[str, Any]
) -> None:
    """Add book metadata to mention."""
    metadata = event_data.get("metadata", {})
    if metadata.get("book_title"):
        mention["book"] = metadata["book_title"]
    if metadata.get("author"):
        mention["author"] = metadata["author"]
    if metadata.get("series"):
        mention["series"] = metadata["series"]
    if metadata.get("chapter_title"):
        mention["chapter"] = metadata["chapter_title"]


def _add_event_names_to_mention(
    mention: Dict[str, Any], event_data: Dict[str, Any]
) -> None:
    """Add event names to mention."""
    if event_data["Event"].get("Event_Name"):
        mention["Event_Name"] = event_data["Event"]["Event_Name"]
    if event_data["Event"].get("Sub-events") and event_data["Event"]["Sub-events"][
        0
    ].get("Sub-event_Name"):
        mention["Sub_event_Name"] = event_data["Event"]["Sub-events"][0][
            "Sub-event_Name"
        ]


def _link_date_to_mention(
    mention: Dict[str, Any], dates_index: Dict[str, Dict[str, str]], output_root: Path
) -> None:
    """Link date to mention."""
    event_id = mention["EventID"]
    sub_event_id = mention["Sub_eventID"]
    date_key = f"{event_id}:{sub_event_id}"
    if date_key not in dates_index:
        return

    mention["DateID"] = dates_index[date_key]["DateID"]
    mention["DateMentionID"] = dates_index[date_key]["DateMentionID"]

    # Add human-readable date
    date_file = output_root / "dates" / f"{dates_index[date_key]['DateID']}.json"
    if date_file.exists():
        with open(date_file, encoding="utf-8") as f:
            date_data = json.load(f)
            if date_data.get("date"):
                mention["date"] = date_data["date"]
    logger.debug("Linked to date %s", mention["DateID"])


def extract_equipment_from_event(
    event_file: Path,
    output_dir: Path,
    grok_client: GrokClient,
    output_root: Optional[Path] = None,
    max_retries: int = 3,
    enable_enrichment: bool = False,
    verify_media_with_vision: bool = True,
) -> List[Path]:
    """Extract equipment from event file.

    Args:
        event_file: Path to event JSON file
        output_dir: Output directory for equipment files
        grok_client: Grok API client
        output_root: Root output directory (defaults to output_dir.parent)
        max_retries: Maximum retry attempts per extraction
        enable_enrichment: Enable external data enrichment (Wikipedia/Grokipedia)
        verify_media_with_vision: Verify media relevance with Grok vision API

    Returns:
        List of created/updated equipment file paths
    """
    logger.info("Extracting equipment from %s", event_file)

    # Setup
    output_dir.mkdir(parents=True, exist_ok=True)
    processed = _load_processed_registry(output_dir)
    if str(event_file) in processed:
        logger.debug("  Already processed, skipping")
        return []

    # Load and validate event data
    event_data = _load_event_data(event_file)
    if not event_data:
        return []

    # Load indices
    output_root = output_root or output_dir.parent
    try:
        people_index, people_groups_index, dates_index = load_entity_indices(
            output_root
        )
        equipment_index = load_equipment_index(output_dir)
    except Exception as e:
        logger.error("Failed to load indices: %s", e)
        return []

    # Extract equipment using LLM
    equipment_list = _extract_equipment_with_llm(event_data, grok_client, max_retries)
    if not equipment_list:
        logger.info("  No equipment extracted")
        return []

    # Process all equipment items
    modified_files = [
        eq_file
        for eq_data in equipment_list
        if (
            eq_file := _process_equipment_item(
                eq_data,
                event_data,
                people_index,
                people_groups_index,
                dates_index,
                output_root,
                output_dir,
                equipment_index,
                grok_client,
                enable_enrichment,
                verify_media_with_vision,
            )
        )
    ]

    # Finalize
    _finalize_extraction(output_dir, modified_files, event_file, processed)
    return modified_files


def generate_equipment_index(equipment_dir: Path) -> None:
    """Generate index.json mapping equipment names to files."""
    index = {}

    for eq_file in equipment_dir.glob("*.json"):
        if eq_file.name == "index.json":
            continue
        try:
            with open(eq_file, encoding="utf-8") as file_handle:
                eq_data = json.load(file_handle)
                index[eq_data["common_name"]] = eq_file.name
        except Exception as e:
            logger.warning("Failed to index %s: %s", eq_file.name, e)

    index_file = equipment_dir / "index.json"
    with open(index_file, "w", encoding="utf-8") as file_handle:
        json.dump(index, file_handle, indent=2, sort_keys=True)

    logger.info("Generated index with %d equipment entries", len(index))


if __name__ == "__main__":
    import sys
    from src.grok_client import GrokClient  # pylint: disable=reimported

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python -m src.extraction.equipment <event_file>")
        sys.exit(1)

    event_file = Path(sys.argv[1])
    output_dir = Path("output/equipment")
    output_root = Path("output")
    cache_dir = Path("cache/api")

    grok = GrokClient(cache_dir)

    files = extract_equipment_from_event(
        event_file,
        output_dir,
        grok,
        output_root,
    )

    print(f"\nCreated {len(files)} equipment files:")
    for file_path in files:
        print(f"  {file_path}")
