"""Link terrain/aerial document photos to the PlaceID they depict (M5, multimodal epic).

HyperWar pages carry landscape photos whose captions describe a PLACE rather than people — "HEDGEROW
COUNTRY", "AERIAL VIEW OF OMAHA BEACH", "TERRAIN ON OMAHA BEACH". M2 captured those captions onto the
parsed images (``Image.caption``); today nothing classifies them as terrain/aerial or links them to
the place they show. This module closes that gap, mirroring the M3 image->person linker.

Design (owner steer: lean on descriptions; when in doubt, ask Grok to describe the subject matter):
  parsed image + caption
    -> CLASSIFY terrain/aerial (deterministic caption keywords first; ambiguous -> Grok vision
       describes the subject matter + classifies)
    -> RESOLVE the depicted place (place names in the caption -> PlaceID via the place name-index;
       when the caption names no resolvable place -> Grok names it from the vision description)
    -> APPEND {url, title=caption, source="document:<book>"} to that place's images[] via the central
       guarded writer (schema-validated, entity="places"), dedup by url.

Fail-safe (never raises into the pipeline); OBSERV image_place.* .
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Deterministic terrain/aerial cues in a caption (lowercased). Conservative: a people/equipment photo
# caption should NOT trip these.
_TERRAIN_CUES = (
    "aerial view",
    "aerial photograph",
    "aerial of",
    "terrain",
    "hedgerow",
    "landscape",
    "topography",
    "beachhead",
    "looking inland",
    "looking toward",
    "view of the",
    "panorama",
)
# "AERIAL VIEW OF <PLACE>" / "TERRAIN ON <PLACE>" / "...OVER <PLACE>" — capture the place tail.
_PLACE_TAIL = re.compile(
    r"\b(?:aerial\s+view\s+of|aerial\s+of|view\s+of|terrain\s+(?:on|at|near)|over|near|at)\s+(.+)",
    re.IGNORECASE,
)


def classify_terrain_aerial(
    caption: str, url: str = "", *, grok_client: Any = None
) -> Tuple[bool, str]:
    """Is this image a terrain/aerial photo of a place? Returns (is_terrain, method).

    Caption-first (deterministic keyword match). When the caption is ambiguous AND a Grok client is
    available, ask vision to describe the subject matter and decide. Fail-safe -> (False, ...).
    """
    text = f"{caption} {url}".lower()
    if any(cue in text for cue in _TERRAIN_CUES):
        return (True, "caption")
    if grok_client is None:
        return (False, "caption")
    return _grok_is_terrain(caption, grok_client)


def _grok_is_terrain(caption: str, grok_client: Any) -> Tuple[bool, str]:
    """Ask Grok to describe the subject matter and say if it is a terrain/landscape/aerial view of a
    place (vs people/equipment/diagram). One-word gate; fail-safe -> (False, 'grok-error').
    """
    try:
        prompt = (
            "A WWII source document shows an image with this caption: "
            f"{caption!r}\nIs the image primarily a TERRAIN, landscape, or AERIAL view of a PLACE "
            "(as opposed to a portrait of people, equipment, or a diagram/chart)?\n"
            "Answer with exactly one word: TERRAIN or OTHER."
        )
        resp = (grok_client.chat_completion(prompt) or "").strip().upper()
        return (resp.startswith("TERRAIN"), "grok")
    except Exception as e:  # noqa: BLE001
        logger.warning("OBSERV image_place.grok_error reason=%s", str(e)[:120])
        return (False, "grok-error")


def _clean_place(raw: str) -> str:
    """Trim a captured place tail to a clean place phrase: cut at a comma / sentence / trailing
    prose cue (dates, 'looking inland', 'during'...), strip leading articles + generic 'port of'
    style prefixes, and drop punctuation."""
    # cut at the first comma, period+space, or trailing-prose cue
    s = re.split(
        r",|\.\s|[;:]|\blooking\b|\bduring\b|\bafter\b|\bbefore\b|\bwhere\b|\bon\s+\d",
        raw,
        maxsplit=1,
    )[0]
    s = s.strip(" ,.-\u2014").strip()
    # strip a leading article / generic locative prefix ("THE PORT OF CHERBOURG" -> "CHERBOURG")
    s = re.sub(
        r"^(?:the\s+)?(?:port|city|town|village|harbou?r|area|region|outskirts)\s+of\s+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"^the\s+", "", s, flags=re.IGNORECASE)
    return s.strip()


def parse_caption_place_names(caption: str) -> List[str]:
    """Candidate place names from a terrain/aerial caption. Handles "AERIAL VIEW OF UTAH/OMAHA BEACH"
    (split on '/' keeps a shared 'BEACH' suffix) and "TERRAIN ON OMAHA BEACH". Order-preserving,
    deduped. Pure; never raises."""
    out: List[str] = []
    seen: set = set()
    try:
        m = _PLACE_TAIL.search(caption or "")
        if not m:
            return out
        for cand in _split_place_tail(m.group(1)):
            cand = _clean_place(cand)
            key = cand.lower()
            if cand and key not in seen and 1 < len(cand) <= 60:
                out.append(cand)
                seen.add(key)
    except Exception as e:  # noqa: BLE001
        logger.debug("parse_caption_place_names skipped: %s", e)
    return out


def _split_place_tail(tail: str) -> List[str]:
    """Split a place tail on '/' or ' and ', preserving a shared trailing NOUN ("UTAH/OMAHA BEACH"
    -> ["UTAH BEACH", "OMAHA BEACH"]). The shared suffix is taken from the LAST part's cleaned
    place phrase so a trailing date/prose never becomes the suffix."""
    tail = tail.strip()
    parts = [p.strip() for p in re.split(r"\s*/\s*|\s+and\s+", tail) if p.strip()]
    if len(parts) <= 1:
        return [tail]
    last_clean = _clean_place(parts[-1])
    suffix = last_clean.split()[-1] if last_clean.split() else ""
    out = []
    for p in parts[:-1]:
        pc = _clean_place(p)
        # a bare single token (e.g. "UTAH") inherits the shared noun suffix ("BEACH")
        if suffix and len(pc.split()) == 1 and not pc.lower().endswith(suffix.lower()):
            out.append(f"{pc} {suffix}")
        else:
            out.append(pc)
    out.append(last_clean)
    return out


def _resolve_or_create_place(
    place_name: str,
    places_dir: Path,
    name_to_id: Dict[str, str],
    index: Dict[str, str],
) -> Optional[Path]:
    """Resolve a described place to its file: an existing place when the name/alias is already
    indexed, otherwise ROUTE THE DESCRIBED PLACE TO THE PLACE PIPELINE (mint a new PlaceID via the
    canonical ``_find_or_create_place``) so a place we can describe is never dropped (owner steer).
    Returns the place file, or None on failure. Fail-safe."""
    try:
        key = place_name.lower().strip()
        if not key:
            return None
        if key in name_to_id or key in index:
            from src.extraction.places import (
                _find_or_create_place,
            )  # existing -> returns its file

            return _find_or_create_place(place_name, {}, places_dir, index)
        # Not yet a place -> create it through the real pipeline (provenance-marked mention).
        from src.extraction.places import _find_or_create_place

        mention = {"geography_type": "other", "source": "document:image-caption"}
        pf = _find_or_create_place(place_name, mention, places_dir, index)
        logger.info("OBSERV image_place.created place=%s", place_name)
        return pf
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV image_place.error place=%s reason=%s", place_name, str(e)[:120]
        )
        return None


def _build_place_image_entry(
    url: str, caption: str, book: str, source_section_id: Optional[str]
) -> Dict[str, Any]:
    """Construct a schema-valid place image entry with durable ULID attribution."""
    entry: Dict[str, Any] = {
        "url": url,
        "title": caption[:300],
        "source": f"document:{book}" if book else "document",
    }
    if source_section_id:
        entry["SourceSectionID"] = source_section_id  # durable ULID attribution
    return entry


def _read_place_id(place_file: Path) -> Optional[str]:
    """PlaceID from a resolved place file, or None. Fail-safe."""
    try:
        import json as _json

        rec = _json.loads(place_file.read_text(encoding="utf-8"))
        return rec.get("PlaceID") if isinstance(rec, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _find_image_record_by_url(images_dir: Path, url: str) -> Optional[Path]:
    """The images/*.json record file whose url matches, or None. Fail-safe."""
    import json as _json

    try:
        for imf in images_dir.glob("*.json"):
            if imf.name == "index.json":
                continue
            try:
                rec = _json.loads(imf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if isinstance(rec, dict) and (rec.get("url") or "") == url:
                return imf
    except Exception:  # noqa: BLE001
        return None
    return None


def _set_image_content_location(
    out: Path, url: str, place_file: Path, place_name: str
) -> bool:
    """Set schema.org ``contentLocation`` ({PlaceID,name}) = the depicted place on the first-class
    image record (out/images/*.json) whose url matches. Deterministic (PlaceID read from the resolved
    place file, not guessed). Idempotent + fail-safe; True iff written."""
    try:
        import json as _json

        images_dir = out / "images"
        if not images_dir.exists():
            return False
        place_id = _read_place_id(place_file)
        if not place_id:
            return False
        imf = _find_image_record_by_url(images_dir, url)
        if imf is None:
            return False
        rec = _json.loads(imf.read_text(encoding="utf-8"))
        existing = rec.get("contentLocation")
        if isinstance(existing, dict) and existing.get("PlaceID") == place_id:
            return False  # idempotent
        rec["contentLocation"] = {"PlaceID": place_id, "name": place_name}
        from src.utils.file_lock import write_json_with_lock

        if write_json_with_lock(imf, rec, entity="images"):
            logger.info(
                "OBSERV image_place.content_location image=%s place=%s",
                imf.stem,
                place_id,
            )
            return True
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("OBSERV image_place.error reason=%s", str(e)[:120])
        return False


def _append_place_image(
    place_file: Path,
    url: str,
    caption: str,
    book: str,
    source_section_id: Optional[str] = None,
) -> bool:
    """Append a document-sourced image entry to a place's images[] (schema-valid
    {url,title,source,SourceSectionID}; dedup by url) via the central guarded writer
    (entity='places'). Fail-safe; True iff written."""
    try:
        rec = json.loads(place_file.read_text(encoding="utf-8"))
        if not isinstance(rec, dict):
            return False
        images = rec.get("images")
        if not isinstance(images, list):
            images = []
        if any(isinstance(e, dict) and e.get("url") == url for e in images):
            return False  # already linked (dedup by url)
        images.append(_build_place_image_entry(url, caption, book, source_section_id))
        rec["images"] = images
        from src.utils.file_lock import write_json_with_lock

        if not write_json_with_lock(place_file, rec, entity="places"):
            logger.warning(
                "OBSERV image_place.write_blocked place=%s — guard refused",
                place_file.name,
            )
            return False
        logger.info(
            "OBSERV image_place.linked place=%s book=%s source_section=%s",
            place_file.stem,
            book,
            source_section_id or "-",
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV image_place.error place=%s reason=%s", place_file.name, str(e)[:120]
        )
        return False


def link_images_to_places(
    parsed_docs: List[Dict[str, Any]],
    places_dir: Path,
    *,
    grok_client: Any = None,
) -> Dict[str, int]:
    """For each parsed doc's captioned images, classify terrain/aerial and link the depicted place
    (resolving to an existing PlaceID or creating one). Returns a summary. Fail-safe; OBSERV.
    """
    summary = {
        "images": 0,
        "terrain": 0,
        "places": 0,
        "linked": 0,
        "skipped": 0,
        "created": 0,
    }
    from src.extraction.places import _build_place_name_index, _load_places_index

    name_to_id, _ = _build_place_name_index(places_dir)
    index = _safe_load_index(places_dir, _load_places_index)
    # CRITICAL provenance: map book -> SourceSectionID ULID so every link is ULID-attributed.
    book_to_section = _build_book_section_index(places_dir.parent)
    for doc in parsed_docs:
        if isinstance(doc, dict):
            _link_doc_images(
                doc,
                places_dir,
                name_to_id,
                index,
                book_to_section,
                grok_client,
                summary,
            )
    logger.info(
        "OBSERV image_place.summary images=%d terrain=%d places=%d created=%d linked=%d skipped=%d",
        summary["images"],
        summary["terrain"],
        summary["places"],
        summary["created"],
        summary["linked"],
        summary["skipped"],
    )
    return summary


def _safe_load_index(places_dir: Path, loader) -> Dict[str, str]:
    """Load the places index via the canonical pipeline loader; {} on any error (fail-safe)."""
    try:
        return loader(places_dir) or {}
    except Exception:  # noqa: BLE001
        return {}


def _section_book_pair(sf: Path) -> Optional[Tuple[str, str]]:
    """(book_lower, SourceSectionID) from a source_section file, or None (skip). Fail-safe."""
    try:
        rec = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(rec, dict):
        return None
    sid = rec.get("SourceSectionID")
    book = (rec.get("source") or {}).get("book")
    if sid and isinstance(book, str) and book.strip():
        return (book.strip().lower(), sid)
    return None


def _build_book_section_index(out: Path) -> Dict[str, str]:
    """Map lowercased book name -> SourceSectionID ULID from the committed source_section records
    (out/source_section/*.json). The source_section is the canonical source entity; this gives every
    image link a durable ULID attribution back to its source. Fail-safe -> {}."""
    out_map: Dict[str, str] = {}
    try:
        sec_dir = out / "source_section"
        if not sec_dir.exists():
            return out_map
        for sf in sec_dir.glob("*.json"):
            pair = _section_book_pair(sf)
            if pair:
                out_map.setdefault(pair[0], pair[1])
    except Exception as e:  # noqa: BLE001
        logger.debug("book->section index skipped: %s", e)
    return out_map


def _link_doc_images(
    doc: Dict[str, Any],
    places_dir: Path,
    name_to_id: Dict[str, str],
    index: Dict[str, str],
    book_to_section: Dict[str, str],
    grok_client: Any,
    summary: Dict[str, int],
) -> None:
    """Classify + link one parsed doc's terrain/aerial images (mutates ``summary``). Fail-safe."""
    book = str(doc.get("book", ""))
    section_id = book_to_section.get(book.strip().lower()) if book else None
    for img in doc.get("images") or []:
        if not isinstance(img, dict):
            continue
        caption = (img.get("caption") or "").strip()
        url = (img.get("url") or "").strip()
        if not caption or not url:
            continue
        summary["images"] += 1
        _link_one_image(
            caption,
            url,
            book,
            section_id,
            places_dir,
            name_to_id,
            index,
            grok_client,
            summary,
        )


def _link_one_image(
    caption: str,
    url: str,
    book: str,
    section_id: Optional[str],
    places_dir: Path,
    name_to_id: Dict[str, str],
    index: Dict[str, str],
    grok_client: Any,
    summary: Dict[str, int],
) -> None:
    """Classify terrain/aerial, resolve/create the place(s), and link the image. Fail-safe."""
    try:
        is_terrain, _method = classify_terrain_aerial(
            caption, url, grok_client=grok_client
        )
        if not is_terrain:
            summary["skipped"] += 1
            logger.info("OBSERV image_place.not_terrain method=%s url=%s", _method, url)
            return
        summary["terrain"] += 1
        logger.info("OBSERV image_place.terrain method=%s url=%s", _method, url)
        names = parse_caption_place_names(caption)
        if not names:
            summary["skipped"] += 1
            logger.info("OBSERV image_place.no_place url=%s", url)
            return
        for name in names:
            summary["places"] += 1
            before = set(index.values())
            pf = _resolve_or_create_place(name, places_dir, name_to_id, index)
            if pf is None:
                summary["skipped"] += 1
                continue
            if set(index.values()) - before:
                summary["created"] += 1
            if _append_place_image(pf, url, caption, book, section_id):
                summary["linked"] += 1
                # Tier-1: also set contentLocation (the depicted PlaceID) on the image record.
                _set_image_content_location(places_dir.parent, url, pf, name)
            else:
                summary["skipped"] += 1
    except Exception as e:  # noqa: BLE001 - linking must never crash the pipeline
        logger.warning("OBSERV image_place.error reason=%s", str(e)[:120])
