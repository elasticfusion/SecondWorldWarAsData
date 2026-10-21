"""Unit-patch acquisition for people_groups (ARTIFACT_STORE_DESIGN.md, M0).

Given candidate image URLs for a military unit, select and durably store the
SINGLE highest-resolution image that Grok VISION confirms is that unit's
shoulder-sleeve insignia / patch. Fail-closed: no vision client, a vision error,
or no confirmed patch -> store nothing (null-over-fake).

Flow:
  1. For each candidate URL, ask Grok vision "is this a unit patch for {unit}?".
  2. For vision-confirmed candidates, download the bytes + measure dimensions.
  3. Keep the LARGEST (width*height) confirmed patch; store its master via
     artifact_store; return a ``unit_patch`` record for the group.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.utils import artifact_store
from src.utils.url_hygiene import clean_url, resolved_host_is_safe

logger = logging.getLogger(__name__)

_MAX_BYTES = 15 * 1024 * 1024  # 15 MB cap per candidate image
# Strict decoded-pixel cap (defuse image/decompression bombs: a few-KB file can
# decode to billions of pixels). ~50 MP is far above any real unit patch.
_MAX_PIXELS = 50_000_000
_VISION_SYSTEM = "You identify military unit insignia in images. Answer strictly."


def _safe_url(url: str) -> Optional[str]:
    """SSRF gate for an attacker-influenceable candidate URL (OpenSERP results).
    Returns the cleaned/normalized https URL iff it is well-formed AND its host
    resolves to ONLY public IPs (DNS-rebinding/metadata-endpoint defense), else
    None. Mirrors the hardened web_results path — raw image/vision fetches must
    not bypass it."""
    cleaned = clean_url(url)
    if not cleaned:
        return None
    try:
        from urllib.parse import urlparse

        host = urlparse(cleaned).hostname or ""
        if not host or resolved_host_is_safe(host) is None:
            return None
    except Exception:  # noqa: BLE001 - unresolvable/invalid -> unsafe
        return None
    return cleaned


def _vision_is_patch(
    image_url: str, unit_name: str, nationality: str, grok_client: Any
) -> bool:
    """Grok-vision verdict: is this image the unit's shoulder-sleeve insignia /
    patch? Fail-closed (no client / unsafe URL / error / non-YES -> False).
    The URL MUST already be SSRF-validated by the caller (extract_json_with_image
    downloads it server-side-of-us)."""
    if not grok_client or not image_url:
        return False
    prompt = (
        f"Does this image show the official shoulder-sleeve insignia (unit patch) "
        f'of the {nationality} military unit "{unit_name}" from World War II?\n'
        "It must be the PATCH/INSIGNIA itself (a cloth/embroidered emblem), NOT a "
        "photograph of soldiers, a map, a vehicle, or a document.\n"
        'Return ONLY JSON: {"is_patch": true|false}.'
    )
    try:
        result = grok_client.extract_json_with_image(
            prompt=prompt,
            image_url=image_url,
            system_prompt=_VISION_SYSTEM,
            temperature=0.0,
            use_cache=True,
            cache_type="unit_patch_vision",
        )
        return bool(isinstance(result, dict) and result.get("is_patch") is True)
    except Exception as e:  # noqa: BLE001 - fail-closed
        logger.debug("patch vision verify errored (reject) for %s: %s", image_url, e)
        return False


def _download(image_url: str) -> Optional[bytes]:
    """Download image bytes (capped, streamed, NO redirects). Caller must pass an
    already-SSRF-validated URL. Redirects are disabled because a redirect reopens
    SSRF (a clean host can 302 -> 169.254.169.254). None on any error."""
    try:
        from src.utils.http_pool import get_session  # type: ignore
    except Exception:  # noqa: BLE001 - fall back to requests
        import requests as _requests

        def get_session():  # type: ignore
            return _requests

    try:
        session = get_session()
        headers = {"User-Agent": "WWII-DataPipeline/1.0 (+research; contact in repo)"}
        resp = session.get(
            image_url, timeout=30, headers=headers, stream=True, allow_redirects=False
        )
        if resp.status_code != 200:
            logger.debug("patch download non-200 (%s): %s", resp.status_code, image_url)
            return None
        buf = bytearray()
        for chunk in resp.iter_content(8192):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > _MAX_BYTES:
                logger.debug("patch candidate exceeds cap, skipping: %s", image_url)
                return None
        return bytes(buf)
    except Exception as e:  # noqa: BLE001
        logger.debug("patch download failed for %s: %s", image_url, e)
        return None


def _dimensions(data: bytes) -> Optional[tuple]:
    """(width, height) via PIL, or None if unreadable / a decompression bomb
    (treated as not a usable image). Enforces a strict decoded-pixel cap."""
    try:
        import io

        from PIL import Image

        # Hard cap decoded pixels (bomb defense); PIL raises if exceeded.
        prev = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.verify()  # force header/bomb check without full decode
            with Image.open(io.BytesIO(data)) as img2:
                w, h = int(img2.width), int(img2.height)
        finally:
            Image.MAX_IMAGE_PIXELS = prev
        if w <= 0 or h <= 0 or (w * h) > _MAX_PIXELS:
            return None
        return (w, h)
    except Exception as e:  # noqa: BLE001 (incl. PIL DecompressionBombError)
        logger.debug("patch candidate not a readable/safe image: %s", e)
        return None


def _ext_for(url: str, content_type: str) -> str:
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"


def _evaluate_patch_candidate(
    cand: Dict[str, str],
    unit_name: str,
    nationality: str,
    grok_client: Any,
    seen: set,
) -> Optional[Dict[str, Any]]:
    """SSRF-gate, vision-confirm, download, and measure one candidate.

    Preserves the exact call order (_safe_url -> _vision_is_patch -> _download -> _dimensions)
    and mutates `seen` for dedup. Returns {url,title,data,width,height,area} or None to skip.
    """
    raw = (cand or {}).get("url", "")
    if not raw:
        return None
    # SSRF gate FIRST: clean + require public-resolving host before ANY fetch (the vision call
    # downloads the URL too). Reject private/metadata/rebind.
    url = _safe_url(raw)
    if not url or url in seen:
        return None
    seen.add(url)
    if not _vision_is_patch(url, unit_name, nationality, grok_client):
        return None
    data = _download(url)
    if not data:
        return None
    dims = _dimensions(data)
    if not dims:  # vision said patch but bytes aren't a readable/safe image -> skip
        return None
    area = dims[0] * dims[1]
    return {
        "url": url,
        "title": (cand or {}).get("title", ""),
        "data": data,
        "width": dims[0],
        "height": dims[1],
        "area": area,
    }


def _select_best_patch(
    candidates: List[Dict[str, str]],
    unit_name: str,
    nationality: str,
    grok_client: Any,
) -> Optional[Dict[str, Any]]:
    """Pick the highest-resolution VISION-CONFIRMED patch among candidates. SSRF-gates each URL
    (clean + public-resolving host) BEFORE any fetch/vision call, dedups, and skips bytes that
    aren't a readable/safe image. Returns {url,title,data,width,height} or None."""
    best: Optional[Dict[str, Any]] = None
    best_area = -1
    seen: set = set()
    for cand in candidates:
        evaluated = _evaluate_patch_candidate(
            cand, unit_name, nationality, grok_client, seen
        )
        if evaluated is None:
            continue
        area = evaluated["area"]
        if area > best_area:
            best_area = area
            best = {
                "url": evaluated["url"],
                "title": evaluated["title"],
                "data": evaluated["data"],
                "width": evaluated["width"],
                "height": evaluated["height"],
            }
    return best


def acquire_unit_patch(
    candidates: List[Dict[str, str]],
    *,
    group_id: str,
    unit_name: str,
    nationality: str,
    grok_client: Any,
    output_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Select + store the single highest-resolution vision-confirmed unit patch.

    ``candidates`` are ``{"url","title",...}`` dicts (e.g. OpenSERP image hits).
    Returns a ``unit_patch`` record (url, local_path, source, caption,
    vision_verified, width, height, sha256) or None if nothing is confirmed.
    Fail-closed + fail-safe: never raises."""
    if not grok_client or not candidates or not group_id:
        return None
    if output_root is None:
        # No synced local store root (e.g. the S3-storage-based SQS worker). Writing
        # a master to ./artifacts there would NEVER sync -> a durable dangling
        # pointer. Skip rather than lie. (M1: give the worker an S3-backed store.)
        logger.debug("unit patch skipped: no synced artifact root (output_root=None)")
        return None

    best = _select_best_patch(candidates, unit_name, nationality, grok_client)

    if not best:
        return None  # null-over-fake: no confirmed patch -> store nothing

    manifest = artifact_store.store_master(
        best["data"],
        source="openserp",
        artifact_id=group_id,
        filename=f"unit_patch{_ext_for(best['url'], '')}",
        content_type="image",
        status="stored",
        provenance={
            "kind": "unit_patch",
            "url": best["url"],
            "group_id": group_id,
            "unit_name": unit_name,
            "nationality": nationality,
        },
        output_root=output_root,
    )
    if not manifest:
        return None

    return {
        "url": best["url"],
        "local_path": manifest["local_path"],
        "source": "openserp",
        "caption": best["title"],
        "vision_verified": True,
        "width": best["width"],
        "height": best["height"],
        "sha256": manifest["sha256"],
    }
