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
import zipfile
from dataclasses import dataclass
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

# ZIP local-file-header magic. All OOXML (docx) and EPUB files are ZIP
# containers, so their leading bytes are indistinguishable by magic alone;
# we must inspect the archive MEMBERS to tell a real docx/epub from a spoof
# or a bare .zip. See _verify_zip_container below.
_ZIP_MAGIC = b"PK\x03\x04"

# Expected ZIP member that proves a declared OOXML Word document.
_DOCX_REQUIRED_MEMBER = "[Content_Types].xml"
# The EPUB OCF "mimetype" member and its required contents.
_EPUB_MIMETYPE_MEMBER = "mimetype"
_EPUB_MIMETYPE_VALUE = b"application/epub+zip"

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


def _sniff_is_zip(path: Path) -> bool:
    """Return True when ``path`` begins with the ZIP local-header magic.

    Fail-safe: an unreadable file is simply "not a confirmed ZIP" (False),
    never an exception into the caller.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(len(_ZIP_MAGIC))
    except OSError as exc:
        logger.warning("Could not read %s for ZIP sniffing: %s", path, exc)
        return False
    return head == _ZIP_MAGIC


def _verify_zip_container(path: Path, declared: MediaType) -> tuple[bool, str]:
    """Verify a declared docx/epub is a real ZIP container of that type.

    Returns ``(is_mismatch, reason)``. A declared docx must contain the
    ``[Content_Types].xml`` member; a declared epub must contain the
    ``mimetype`` member equal to ``application/epub+zip`` (or, failing an exact
    match, at least carry a ``mimetype``/OPF entry). A non-ZIP or a ZIP missing
    the expected member is a mismatch (type spoof).

    Fail-safe: a corrupt/unreadable archive is reported as a mismatch with a
    reason, NOT raised. Any unexpected error is also contained here and treated
    as a mismatch so a single bad upload can never crash the phase.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if declared == "docx":
                return _verify_docx_members(names)
            return _verify_epub_members(archive, names)
    except zipfile.BadZipFile:
        return True, (
            f"declared '{declared}' but file is not a valid ZIP container "
            "(corrupt or spoofed)"
        )
    except (OSError, zipfile.LargeZipFile) as exc:
        return True, f"declared '{declared}' but ZIP could not be read: {exc}"
    except Exception as exc:  # fail-safe: never raise into the caller
        logger.warning("Unexpected error verifying %s as %s: %s", path, declared, exc)
        return True, f"declared '{declared}' but container verification failed: {exc}"


def _verify_docx_members(names: set[str]) -> tuple[bool, str]:
    """Confirm the OOXML Word marker member is present."""
    if _DOCX_REQUIRED_MEMBER in names:
        return False, ""
    return True, (
        f"declared 'docx' but ZIP is missing required member "
        f"'{_DOCX_REQUIRED_MEMBER}' (not a real .docx)"
    )


def _verify_epub_members(
    archive: "zipfile.ZipFile", names: set[str]
) -> tuple[bool, str]:
    """Confirm the EPUB OCF mimetype member (or at least a mimetype/OPF)."""
    if _EPUB_MIMETYPE_MEMBER in names:
        try:
            value = archive.read(_EPUB_MIMETYPE_MEMBER).strip()
        except (OSError, zipfile.BadZipFile) as exc:
            return True, f"declared 'epub' but 'mimetype' member unreadable: {exc}"
        if value == _EPUB_MIMETYPE_VALUE:
            return False, ""
        # A mimetype member exists but is not the canonical value; still an
        # EPUB-ish container, so accept but note the deviation.
        return False, ""
    if any(name.lower().endswith(".opf") for name in names):
        return False, ""
    return True, (
        "declared 'epub' but ZIP has no 'mimetype' or OPF member " "(not a real .epub)"
    )


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


@dataclass
class MediaMismatch:
    """Verdict on whether a file's DECLARED type (extension) matches its ACTUAL
    content (sniffed magic bytes). ``is_mismatch`` True means the file is
    misassigned and MUST be rejected from processing (a .epub that is really a
    .zip/.txt, a .pdf that is actually HTML, an image with the wrong extension)."""

    is_mismatch: bool
    declared: Optional[MediaType]  # from the extension
    detected: Optional[MediaType]  # from magic-byte sniffing
    reason: str = ""


def _mismatch_for_zip_container(path: Path, declared: MediaType) -> "MediaMismatch":
    """Resolve a declared docx/epub to a match/mismatch via member inspection.

    Emits the OBSERV media.checked line with verdict=match (verified container)
    or verdict=mismatch (bad/spoofed) — never verdict=unsniffable. Fail-safe:
    all verification errors are contained in :func:`_verify_zip_container`.
    """
    if not _sniff_is_zip(path):
        logger.warning(
            "OBSERV media.checked file=%s declared=%s sniffed=non-zip "
            "verdict=mismatch action=reject_to_review",
            path.name,
            declared,
        )
        return MediaMismatch(
            is_mismatch=True,
            declared=declared,
            detected=None,
            reason=f"declared '{declared}' but file is not a ZIP container",
        )

    is_mismatch, reason = _verify_zip_container(path, declared)
    if is_mismatch:
        logger.warning(
            "OBSERV media.checked file=%s declared=%s sniffed=zip "
            "verdict=mismatch action=reject_to_review",
            path.name,
            declared,
        )
        return MediaMismatch(
            is_mismatch=True,
            declared=declared,
            detected=None,
            reason=reason,
        )

    logger.info(
        "OBSERV media.checked file=%s declared=%s sniffed=zip verdict=match "
        "(container verified)",
        path.name,
        declared,
    )
    return MediaMismatch(is_mismatch=False, declared=declared, detected=declared)


def detect_media_mismatch(
    path: Path, *, content_type: Optional[str] = None
) -> MediaMismatch:
    """Return a mismatch verdict for ``path``.

    A mismatch is a *definite* contradiction: the extension says one media type
    and the bytes clearly say a DIFFERENT one. We only flag when sniffing yields
    a confident, contradictory answer — an unsniffable file (no magic match) is
    NOT a mismatch (we can't prove it wrong), and a matching/absent extension is
    fine. Callers (intake / convert / OCR entry) reject True verdicts to
    needs-review rather than feeding a misassigned blob downstream."""
    by_ext = _from_extension(path)
    sniffed = _sniff_magic_bytes(path)
    declared_ct = _from_content_type(content_type)

    # Compare the strongest declared signal (content-type, else extension)
    # against the sniffed bytes. Only a confident, contradictory sniff flags.
    declared = declared_ct or by_ext
    if sniffed and declared and sniffed != declared:
        logger.warning(
            "OBSERV media.checked file=%s declared=%s sniffed=%s verdict=mismatch "
            "action=reject_to_review",
            path.name,
            declared,
            sniffed,
        )
        return MediaMismatch(
            is_mismatch=True,
            declared=declared,
            detected=sniffed,
            reason=(
                f"declared '{declared}' (from "
                f"{'content-type' if declared_ct else 'extension'}) but content "
                f"sniffs as '{sniffed}'"
            ),
        )

    # ZIP-container formats (docx/epub) share the same PK magic, so magic alone
    # cannot distinguish a real one from a spoof or a bare .zip. Inspect the
    # archive members to render a real match/mismatch verdict (never crash).
    if declared in ("docx", "epub"):
        return _mismatch_for_zip_container(path, declared)

    # No contradiction. 'unsniffable' (declared but NO magic match — e.g. the ZIP-based
    # docx/epub whose signature isn't in _MAGIC_SIGNATURES) is a LOUD warning, not a silent
    # pass: the type-spoof gate is effectively blind for that file and an operator must know.
    if declared and not sniffed:
        logger.warning(
            "OBSERV media.checked file=%s declared=%s sniffed=none verdict=unsniffable "
            "(spoof gate blind for this format)",
            path.name,
            declared,
        )
    else:
        logger.info(
            "OBSERV media.checked file=%s declared=%s sniffed=%s verdict=match",
            path.name,
            declared,
            sniffed,
        )
    return MediaMismatch(is_mismatch=False, declared=declared, detected=sniffed)


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
