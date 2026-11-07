"""Phase 3: resolve images whose map-vs-image decision was DEFERRED (``map_check=pending_vision``).

Phase 2 classifies document images with the shared rule (src/extraction/media_classify): ibiblio
URL paths decide deterministically; every other host is ambiguous and is marked
``pending_vision`` — Grok vision is deferred to Phase 3 (here), never skipped.

For each pending image record:
  * vision says MAP   -> persist a Map entity through the SAME in-doc map path
    (map_link_source.persist_map_entities: URL-keyed find-or-create, footprint, linking) and mark
    the image ``map_check=map`` + ``superseded_by_map_id`` (provenance both ways, no silent loss).
  * vision says NOT   -> ``map_check=not_map``.
  * vision undecided  -> left pending (retried next run) and counted; any remaining pending images
    raise ONE actionable alert at the end of the step.

A Phase 2 vision caption verdict, when present, is reused (no second Grok call). An ibiblio
``maps/`` image the parser did not list in maps[] arrives as ``map_check=map`` without a Map and is
persisted here without vision.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from src.extraction.media_classify import (
    MAP_CHECK_MAP,
    MAP_CHECK_NOT_MAP,
    MAP_CHECK_PENDING,
    verdict_from_classification,
    vision_is_map,
)
from src.utils.observe import observe_warn

logger = logging.getLogger(__name__)


_MAX_LOCAL_BYTES = 25 * 1024 * 1024  # same cap as the Phase 2 image fetch


def _image_bytes(rec: Dict[str, Any], output_root: Path) -> Optional[bytes]:
    """Local copy first — ONLY from output/images/files/ (by basename) and size-capped, so a
    tampered record cannot send another file to Grok — else an SSRF-safe fetch of the URL.
    """
    local = rec.get("local_copy")
    if local:
        p = output_root / "images" / "files" / Path(str(local)).name
        if p.is_file() and p.stat().st_size <= _MAX_LOCAL_BYTES:
            return p.read_bytes()
    url = rec.get("url")
    if not url:
        return None
    from src.extraction.images import fetch_image_bytes

    body, _ext = fetch_image_bytes(url)
    return body


def _context(rec: Dict[str, Any]) -> Dict[str, str]:
    return {
        "alt_text": str(rec.get("image_title") or ""),
        "sub_event_name": str(rec.get("Sub-event_Name") or ""),
        "place_name": str(rec.get("place_name") or ""),
        "date": str(rec.get("date") or ""),
        "book": _book_of(rec),
    }


def _book_of(rec: Dict[str, Any]) -> str:
    from src.extraction.images import book_from_image_source

    return book_from_image_source(rec)


def _persist_as_map(
    rec: Dict[str, Any],
    output_root: Path,
    checked_at: Optional[str],
    download: bool,
    grok_client: Any = None,
) -> Optional[str]:
    """Create/append the Map entity for an image vision identified as a map. Returns the MapID."""
    from src.extraction.map_link_source import _load_url_index, persist_map_entities

    from src.utils.url_hygiene import clean_url

    url = clean_url(str(rec.get("url") or "")) or ""
    if not url:
        return None  # never publish an unvetted URL as a Map source
    refs = []
    if rec.get("Sub-eventID"):
        # The image's located sub-event is its footprint; the paragraph is not known for images
        # (null-over-guess). source_image_id keeps provenance back to the image record.
        refs.append(
            {
                "paragraph_number": None,
                "Sub_eventID": rec["Sub-eventID"],
                "source_image_id": rec.get("ImageID"),
            }
        )
    maps_dir = output_root / "maps"
    persist_map_entities(
        [
            {
                "book": _book_of(rec),
                "maps": [
                    {
                        "url": url,
                        "map_id": url.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                        "description": rec.get("image_title") or "",
                        "references": refs,
                        "is_map": True,
                        "map_vision_checked_at": checked_at,
                    }
                ],
            }
        ],
        maps_dir,
        download=download,
        output_root=output_root,
        grok_client=grok_client,
    )
    filename = _load_url_index(maps_dir / "index.json").get(url)
    return Path(filename).stem if filename else None


def _decide(rec: Dict[str, Any], output_root: Path, grok_client):
    """(is_map, classification, description). Reuses a Phase 2 vision caption verdict when one
    exists (no second Grok call); otherwise asks vision. is_map None = undecided."""
    stored = verdict_from_classification(
        str(rec.get("caption_method") or ""),
        str(rec.get("vision_classification") or ""),
    )
    if stored is not None:
        return stored, rec.get("vision_classification"), ""
    is_map, caption = vision_is_map(
        _image_bytes(rec, output_root), _context(rec), grok_client
    )
    if is_map is None or caption is None:
        return None, None, ""
    return is_map, caption.classification, caption.description


# After this many undecided vision attempts the image is handed to a human (needs_review) instead
# of being re-sent to Grok on every run (cost cap). It stays pending and keeps alerting.
MAX_VISION_ATTEMPTS = 3


def _vision_step(rec: Dict[str, Any], output_root: Path, grok_client) -> str:
    """Decide a pending image's map-vs-image. Returns 'map' | 'not_map' | 'undecided' |
    'needs_human' (attempt cap reached)."""
    attempts = int(rec.get("map_vision_attempts") or 0)
    if attempts >= MAX_VISION_ATTEMPTS:
        rec["needs_review"] = True
        return "needs_human"
    is_map, classification, description = _decide(rec, output_root, grok_client)
    if is_map is None:
        rec["map_vision_attempts"] = attempts + 1
        return "undecided"
    rec["is_map"] = is_map
    rec["map_vision_checked_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    rec["map_check_basis"] = "grok_vision"
    rec["vision_classification"] = classification
    if description and not rec.get("description"):
        rec["description"] = description
    if not is_map:
        rec["map_check"] = MAP_CHECK_NOT_MAP
        return "not_map"
    return "map"


def _resolve_one(
    rec: Dict[str, Any], output_root: Path, grok_client, download: bool
) -> str:
    """Decide/persist one image in place. Returns 'map' | 'not_map' | 'undecided' |
    'needs_human' | 'persist_failed'.

    Two inputs: ``pending_vision`` (ambiguous -> vision decides) and an already-decided
    ``map_check=map`` with no Map yet (an ibiblio maps/ image the parser did not list in maps[])
    -> persisted as a Map WITHOUT vision (so no vision timestamp is claimed for it)."""
    already_map = rec.get("map_check_basis") == "grok_vision" and rec.get("is_map")
    if rec.get("map_check") == MAP_CHECK_PENDING and not already_map:
        # (already_map: a previous run's vision said MAP but the Map could not be saved —
        # retry only the save, never re-ask Grok.)
        outcome = _vision_step(rec, output_root, grok_client)
        if outcome != "map":
            return outcome
    vision_at = (
        rec.get("map_vision_checked_at")
        if rec.get("map_check_basis") == "grok_vision"
        else None
    )
    map_id = _persist_as_map(rec, output_root, vision_at, download, grok_client)
    if not map_id:
        # Classified as a map but the Map could not be created: keep it retryable (pending) WITH
        # its vision verdict, so the next run retries only the save (no second Grok call).
        if rec.get("map_check") != MAP_CHECK_MAP:
            rec["map_check"] = MAP_CHECK_PENDING
        return "persist_failed"
    rec["map_check"] = MAP_CHECK_MAP
    rec["is_map"] = True
    rec["content_type"] = "map"
    rec["superseded_by_map_id"] = map_id
    return "map"


def _needs_resolution(rec: Any) -> bool:
    if not isinstance(rec, dict):
        return False
    check = rec.get("map_check")
    return check == MAP_CHECK_PENDING or (
        check == MAP_CHECK_MAP and not rec.get("superseded_by_map_id")
    )


def _resolve_file(
    f: Path, output_root: Path, grok_client, download: bool
) -> Optional[str]:
    """Resolve one image file. Returns the outcome, 'unreadable', or None (not pending)."""
    from src.utils.file_lock import write_json_with_lock

    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(
            "OBSERV images.map_vision_unreadable image=%s err=%s status=warn",
            f.name,
            type(e).__name__,
        )
        return "unreadable"
    if not _needs_resolution(rec):
        return None
    try:
        outcome = _resolve_one(rec, output_root, grok_client, download)
    except Exception as e:  # noqa: BLE001 - one image must not abort the step
        logger.warning(
            "OBSERV images.map_vision_error image=%s err=%s status=warn",
            f.name,
            type(e).__name__,
        )
        return "undecided"
    # Every outcome changes the record (verdict, attempt counter, needs_review) — persist it so
    # the next run neither loses the verdict nor re-asks Grok.
    if not write_json_with_lock(f, rec, entity="images"):
        # The guarded write was refused: the verdict did not persist — count it as undecided so
        # the end-of-step alert fires (it would otherwise be re-asked silently next run).
        logger.warning(
            "OBSERV images.map_vision_write_refused image=%s outcome=%s status=warn",
            f.name,
            outcome,
        )
        return "undecided"
    return outcome


# One actionable alert per cause (each says what to do), never a generic "something failed".
_ALERTS = {
    "undecided": (
        "map_vision_incomplete",
        "images still unclassified (map vs image) and NOT yet on the map pages; check "
        "XAI_API_KEY / Grok vision errors and image fetch (SSRF/404) in this task's log, then "
        "re-run Phase 3 for the book",
    ),
    "unreadable": (
        "map_vision_unreadable",
        "image records could not be read (see images.map_vision_unreadable lines for the "
        "files); re-run Phase 2 for the book to rewrite them",
    ),
    "needs_human": (
        "map_vision_needs_human",
        f"vision could not decide after {MAX_VISION_ATTEMPTS} attempts; these images are marked "
        "needs_review — open each and set map_check to map or not_map by hand",
    ),
    "persist_failed": (
        "map_persist_failed",
        "images classified as maps could not be saved as Map entities (URL rejected or write "
        "failed; see maps.* lines in this task's log); fix the cause and re-run Phase 3",
    ),
}


def _report(counts: Dict[str, int]) -> None:
    bad = any(counts[k] for k in _ALERTS)
    logger.info(
        "OBSERV images.map_vision pending=%d map=%d not_map=%d undecided=%d needs_human=%d "
        "persist_failed=%d unreadable=%d status=%s",
        counts["pending"],
        counts["map"],
        counts["not_map"],
        counts["undecided"],
        counts["needs_human"],
        counts["persist_failed"],
        counts["unreadable"],
        "warn" if bad else "ok",
    )
    for key, (event, note) in _ALERTS.items():
        if counts[key]:
            observe_warn("images", event, note=note, count=counts[key])


def resolve_pending(
    output_root: Path, grok_client, download: bool = True
) -> Dict[str, int]:
    """Run deferred map vision over every ``pending_vision`` image record. Never raises."""
    counts: Dict[str, int] = {
        k: 0
        for k in (
            "pending",
            "map",
            "not_map",
            "undecided",
            "needs_human",
            "persist_failed",
            "unreadable",
        )
    }
    images_dir = output_root / "images"
    files = sorted(images_dir.glob("*.json")) if images_dir.is_dir() else []
    for f in files:
        if f.name == "index.json":
            continue
        outcome = _resolve_file(f, output_root, grok_client, download)
        if outcome is None:
            continue
        if outcome != "unreadable":
            counts["pending"] += 1
        counts[outcome] += 1
    _report(counts)
    return counts
