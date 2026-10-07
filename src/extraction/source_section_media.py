"""Wikipedia MEDIA fetch for the source_section entity (Phase 2, step 5).

Pulls photos + maps from a derived operation's Wikipedia article and writes them as
FIRST-CLASS ``images`` records (output/images/*.json), cross-linked back to the section by
``SourceSectionID`` + ``EventID``, with full provenance (source_url, license, attribution;
null-over-fake — a missing license is null, never guessed).

Wikipedia only (Grokipedia has no media). Maps are tagged ``image_type: "map"`` and captured
as image records too — the GeoJSON ``map_features`` entity is produced ONLY by the Grok-vision
map-interior extractor, not fabricated here; a captured map raster can later feed that
extractor (the "section -> map" path).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import ulid

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_MAX_MEDIA = 25  # bound how many images we pull per section

# File-name hints that a Commons image is a MAP rather than a photo.
_MAP_HINTS = ("map", "karte", "carte", "offensive", "front", "advance", "_de_", "plan")
# Skip chrome/icon/flag/UI clutter that Wikipedia embeds (not real content media).
_SKIP_HINTS = (
    "icon",
    "logo",
    "flag_of",
    "commons-logo",
    "edit-",
    "ambox",
    "wiki.png",
    "protection",
    "shackle",
    "symbol",
    "wiktionary",
    "wikisource",
    "wikidata",
    "padlock",
    "question_book",
    "red_pog",
    "disambig",
    "arrow",
    "_logo",
    "ooui",
)


def _classify(filename: str) -> Optional[str]:
    """Return 'map' | 'photo' for a real content image, or None to skip chrome/icons."""
    low = filename.lower()
    if any(h in low for h in _SKIP_HINTS):
        return None
    if not low.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".svg")):
        return None
    return "map" if any(h in low for h in _MAP_HINTS) else "photo"


def _list_article_image_titles(title: str, timeout: int) -> List[str]:
    """List the File: titles embedded in a Wikipedia article (action=parse prop=images)."""
    try:
        resp = requests.get(
            _WIKI_API,
            params={
                "action": "parse",
                "format": "json",
                "page": title,
                "prop": "images",
                "redirects": "1",
            },
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        imgs = (resp.json().get("parse") or {}).get("images") or []
        return [f"File:{name}" for name in imgs]
    except (requests.RequestException, ValueError) as e:
        logger.debug("wiki image list failed for %r: %s", title, e)
        return []


def _resolve_commons_image(file_title: str, timeout: int) -> Optional[Dict[str, Any]]:
    """Resolve a File: title to its URL + license metadata via Commons imageinfo."""
    try:
        resp = requests.get(
            _COMMONS_API,
            params={
                "action": "query",
                "format": "json",
                "titles": file_title,
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime",
            },
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        pages = (resp.json().get("query") or {}).get("pages") or {}
        for _pid, pdata in pages.items():
            ii = (pdata.get("imageinfo") or [None])[0]
            if not ii:
                continue
            meta = ii.get("extmetadata") or {}

            def _m(key: str) -> Optional[str]:
                v = meta.get(key) or {}
                val = v.get("value")
                return (
                    re.sub(r"<[^>]+>", "", val).strip()
                    if isinstance(val, str)
                    else None
                )

            return {
                "url": ii.get("url"),
                "mime": ii.get("mime"),
                # null-over-fake: these are None when Commons does not assert them.
                "license": _m("LicenseShortName"),
                "attribution": _m("Artist"),
                "copyright_status": _m("Copyrighted"),
            }
        return None
    except (requests.RequestException, ValueError) as e:
        logger.debug("commons resolve failed for %r: %s", file_title, e)
        return None


def _build_image_record(
    *,
    source_section_id: str,
    event_id: Optional[str],
    file_title: str,
    kind: str,
    resolved: Dict[str, Any],
    local_copy: Optional[str],
) -> Dict[str, Any]:
    """Build a schema-valid images record from resolved Wikipedia/Commons media."""
    url = resolved.get("url")
    return {
        "ImageID": str(ulid.new()),
        "image_title": file_title.replace("File:", ""),
        "image_type": kind,  # "map" | "photo"
        "content_type": kind,
        "resource_type": "online" if url else "offline",
        "source": "Wikipedia / Wikimedia Commons",
        "url": url,
        "local_copy": local_copy,
        "url_capture_date": datetime.now(timezone.utc).isoformat(),
        "license": resolved.get("license"),  # null-over-fake
        "description": resolved.get("attribution"),
        "extracted_date": datetime.now(timezone.utc).isoformat(),
        "EventID": event_id,
        "SourceSectionID": source_section_id,
    }


def fetch_section_media(
    record: Dict[str, Any],
    output_dir: Path,
    download: bool = True,
    timeout: int = 20,
) -> int:
    """Pull Wikipedia photos + maps for the section's operation and write first-class images
    records cross-linked by SourceSectionID + EventID. Returns the count written. No-op (0)
    when there is no operation. Each image failing is non-fatal."""
    operation = record.get("operation")
    if not operation:
        return 0
    # Idempotency: stamp a media gate marker up front so a re-run never re-fetches or writes
    # duplicate image records. Stamped even when the article yields no media.
    if record.get("media_checked_at"):
        return 0
    from datetime import date as _date

    record["media_checked_at"] = _date.today().isoformat()
    title = operation.get("wikipedia_title") or operation.get("name")
    if not title:
        return 0
    section_id = record.get("SourceSectionID")
    event_id = record.get("EventID")

    from src.extraction.images import _download_image
    from src.utils.file_lock import write_json_with_lock

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = images_dir / "files"

    written = 0
    for file_title in _list_article_image_titles(title, timeout)[:_MAX_MEDIA]:
        kind = _classify(file_title)
        if kind is None:
            continue
        resolved = _resolve_commons_image(file_title, timeout)
        if not resolved or not resolved.get("url"):
            continue
        img_id = str(ulid.new())
        local_copy = None
        if download:
            local_copy, _fmt = _download_image(resolved["url"], img_id, storage_dir)
        rec = _build_image_record(
            source_section_id=section_id,
            event_id=event_id,
            file_title=file_title,
            kind=kind,
            resolved=resolved,
            local_copy=local_copy,
        )
        rec["ImageID"] = img_id
        write_json_with_lock(images_dir / f"{img_id}.json", rec, entity="images")
        written += 1
    logger.info(
        "source_section media: wrote %d image(s) for operation %r", written, title
    )
    return written
