"""Document-level media-type detection for the ingestion front-end.

Detects the media type of an original document and produces a
:class:`SourceMetadata` record. Recognized-but-unsupported media (e.g. moving
images today) is recorded and flagged rather than raising, so a run is never
failed by an unsupported input.

Detection order (cheapest, most reliable signal first):
  1. Explicit content-type supplied by the acquirer (if any).
  2. File extension.
  3. Content sniffing (magic bytes) to confirm/override the extension.

See docs/current/dataquality/INGESTION_FRONT_END.md (step 2).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.ingestion.source_metadata import (
    AcquisitionMethod,
    MediaType,
    SourceMetadata,
    compute_checksum,
)

logger = logging.getLogger(__name__)

# Which detected media types the downstream pipeline currently handles.
SUPPORTED_MEDIA: frozenset[MediaType] = frozenset(
    {"pdf", "html", "image", "docx", "epub", "text"}
)

# Extension -> media type.
_EXTENSION_MAP: dict[str, MediaType] = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".docx": "docx",
    ".epub": "epub",
    ".txt": "text",
    ".text": "text",
    ".md": "text",
    ".markdown": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".mp4": "moving_image",
    ".mov": "moving_image",
    ".avi": "moving_image",
    ".mkv": "moving_image",
    ".webm": "moving_image",
    ".m4v": "moving_image",
}

# Normalized content-type token -> media type (value before any ";" parameters).
_CONTENT_TYPE_MAP: dict[str, MediaType] = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",  # noqa: E501
    "application/epub+zip": "epub",
    "text/plain": "text",
    "text/markdown": "text",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/tiff": "image",
    "image/bmp": "image",
    "video/mp4": "moving_image",
    "video/quicktime": "moving_image",
    "video/x-msvideo": "moving_image",
    "video/x-matroska": "moving_image",
    "video/webm": "moving_image",
}

_SNIFF_BYTES = 16

# Fixed-prefix magic-byte signatures: (byte offset, prefix, media type).
# Checked in order; the first match wins. Irregular formats whose signature is
# not a single contiguous prefix (WEBP, HTML) are handled separately below.
_MAGIC_SIGNATURES: tuple[tuple[int, bytes, MediaType], ...] = (
    (0, b"%PDF-", "pdf"),
    (0, b"\x89PNG\r\n\x1a\n", "image"),
    (0, b"\xff\xd8\xff", "image"),  # JPEG
    (0, b"GIF87a", "image"),
    (0, b"GIF89a", "image"),
)


def _from_content_type(content_type: Optional[str]) -> Optional[MediaType]:
    """Map an HTTP-style content-type header to a media type, if recognized."""
    if not content_type:
        return None
    token = content_type.split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_MAP.get(token)


def _from_extension(path: Path) -> Optional[MediaType]:
    """Map a file extension to a media type, if recognized."""
    return _EXTENSION_MAP.get(path.suffix.lower())


def _sniff_magic_bytes(path: Path) -> Optional[MediaType]:
    """Confirm media type from the file's leading bytes, when unambiguous."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError as exc:
        logger.warning("Could not read %s for sniffing: %s", path, exc)
        return None

    for offset, prefix, media_type in _MAGIC_SIGNATURES:
        if head[offset : offset + len(prefix)] == prefix:
            return media_type
    # WEBP: "RIFF" then a 4-byte size, then "WEBP" (non-contiguous prefix).
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    # HTML is text; sniff a lowercased prefix for a tag/doctype.
    lowered = head.lstrip().lower()
    if lowered.startswith(b"<!doctype html") or lowered.startswith(b"<html"):
        return "html"
    return None


def detect_media_type(
    path: Path,
    *,
    content_type: Optional[str] = None,
) -> MediaType:
    """Detect the media type of ``path``.

    Uses, in order: an explicit ``content_type`` (e.g. from an HTTP response),
    the file extension, and magic-byte sniffing to confirm/override. Returns
    ``"unsupported"`` when nothing recognizes the input.
    """
    declared = _from_content_type(content_type)
    by_ext = _from_extension(path)
    sniffed = _sniff_magic_bytes(path)

    # Prefer sniffed evidence when it contradicts the extension (e.g. a PDF
    # saved with the wrong suffix), since bytes are more reliable than names.
    if sniffed and by_ext and sniffed != by_ext:
        logger.info(
            "Media sniff (%s) overrides extension (%s) for %s",
            sniffed,
            by_ext,
            path,
        )
        resolved: Optional[MediaType] = sniffed
    else:
        resolved = declared or by_ext or sniffed

    return resolved or "unsupported"


def build_source_metadata(
    source_id: str,
    path: Path,
    *,
    content_type: Optional[str] = None,
    acquisition_method: AcquisitionMethod = "unknown",
    acquisition_url: Optional[str] = None,
    with_checksum: bool = True,
) -> SourceMetadata:
    """Detect media type and return a populated :class:`SourceMetadata`.

    Unsupported media is recorded with ``supported=False`` and an explanatory
    note; this function does not raise for unsupported inputs. It does surface
    a missing file, since that is a caller error rather than an unsupported
    document.
    """
    if not path.exists():
        raise FileNotFoundError(f"Original document not found: {path}")

    media_type = detect_media_type(path, content_type=content_type)
    supported = media_type in SUPPORTED_MEDIA

    notes = ""
    if not supported:
        if media_type == "unsupported":
            notes = (
                "Unrecognized media type; not processed. "
                "Extension/content-type/magic-bytes did not match a known type."
            )
        else:
            notes = f"Recognized media type '{media_type}' is not yet supported."
        logger.warning("Unsupported source %s: %s", path, notes)

    checksum = compute_checksum(path) if with_checksum else None

    return SourceMetadata(
        source_id=source_id,
        original_path=path,
        media_type=media_type,
        supported=supported,
        acquisition_method=acquisition_method,
        acquisition_url=acquisition_url,
        checksum=checksum,
        notes=notes,
    )
