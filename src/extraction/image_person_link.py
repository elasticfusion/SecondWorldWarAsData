"""Link document photos to the PersonIDs they depict (M3, multimodal epic).

M2 captured figure captions onto parsed images (``Image.caption``), e.g. "ALLIED INVASION CHIEFS.
Left to right: General Bradley, Admiral Ramsay, ...". This module closes the richest multimodal
loss: it resolves those depicted names to EXISTING PersonIDs (via the people name-index) and writes
a document-sourced entry into each person's ``images[]`` — so a named person is linked to the photo
that depicts them.

Design (reuse-first, caption-driven):
  parsed image + caption -> parse_caption_person_names -> resolve name -> PersonID (people index,
  full-name then surname) -> append {url, title=caption, source="document:<book>"} to people.images[]
  via the central guarded writer (schema-validated). Vision (ImageCaptioner) is an OPTIONAL gate:
  when available, only link images classified as a photograph (skip map/diagram/terrain); without a
  client, the caption itself is the signal.

Pure of side effects beyond the people.images[] writes; fail-safe (never raises into the pipeline).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.extraction.caption_names import parse_caption_person_names
from src.utils.text_utils import normalize_name

logger = logging.getLogger(__name__)


def _build_people_index(people_dir: Path) -> Tuple[Dict[str, Path], Dict[str, Path]]:
    """(full-name -> file, surname -> file) indexes over existing people records. Surname maps only
    when UNAMBIGUOUS (a surname shared by >1 person is dropped to avoid mislinking). Fail-safe.
    """
    full: Dict[str, Path] = {}
    surname_counts: Dict[str, int] = {}
    surname: Dict[str, Path] = {}
    try:
        for pf in sorted(people_dir.glob("*.json")):
            norm = _read_person_norm_name(pf)
            if not norm:
                continue
            full.setdefault(norm, pf)
            last = norm.split()[-1]
            surname_counts[last] = surname_counts.get(last, 0) + 1
            surname[last] = pf
    except Exception as e:  # noqa: BLE001
        logger.debug("people index build skipped: %s", e)
    # Drop ambiguous surnames (>1 person shares it).
    surname = {k: v for k, v in surname.items() if surname_counts.get(k, 0) == 1}
    return full, surname


def _read_person_norm_name(pf: Path) -> str:
    """Normalized name from a person record file, or "" (skip index/report/non-dict/no-name)."""
    if pf.name in ("index.json", "duplicate_report.json"):
        return ""
    try:
        rec = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    name = rec.get("name") if isinstance(rec, dict) else None
    if not isinstance(name, str) or not name.strip():
        return ""
    norm = normalize_name(name)
    return norm if (norm and norm.split()) else ""


def _resolve_person_file(
    name: str, full: Dict[str, Path], surname: Dict[str, Path]
) -> Optional[Path]:
    """Resolve a caption name to an existing person file: exact normalized full-name match first,
    then an UNAMBIGUOUS surname match (caption "Bradley" -> record "Omar Bradley"). None if no
    confident match."""
    norm = normalize_name(name)
    if not norm:
        return None
    if norm in full:
        return full[norm]
    toks = norm.split()
    last = toks[-1] if toks else ""
    # Only use surname fallback for a bare surname (1 token) or when the last token resolves
    # unambiguously; never for an ambiguous surname.
    if last and last in surname:
        return surname[last]
    return None


def link_images_to_people(
    parsed_docs: List[Dict[str, Any]],
    people_dir: Path,
    *,
    grok_client: Any = None,
) -> Dict[str, int]:
    """For each parsed doc's captioned images, link depicted people to their PersonID by appending a
    document-sourced entry to people.images[]. Returns a summary. Fail-safe; OBSERV image_person.*.
    """
    summary = {"images": 0, "people_photos": 0, "names": 0, "linked": 0, "skipped": 0}
    full, surname = _build_people_index(people_dir)
    if not full:
        logger.info(
            "OBSERV image_person.summary people_index=0 — nothing to link against"
        )
        return summary
    for doc in parsed_docs:
        if isinstance(doc, dict):
            _link_doc_images(doc, full, surname, grok_client, summary)
    logger.info(
        "OBSERV image_person.summary images=%d people_photos=%d names=%d linked=%d skipped=%d",
        summary["images"],
        summary["people_photos"],
        summary["names"],
        summary["linked"],
        summary["skipped"],
    )
    return summary


def _link_doc_images(
    doc: Dict[str, Any],
    full: Dict[str, Path],
    surname: Dict[str, Path],
    grok_client: Any,
    summary: Dict[str, int],
) -> None:
    """Iterate one parsed doc's captioned images and link each (mutates ``summary``)."""
    book = str(doc.get("book", ""))
    for img in doc.get("images") or []:
        if not isinstance(img, dict):
            continue
        caption = (img.get("caption") or "").strip()
        url = (img.get("url") or "").strip()
        if not caption or not url:
            continue
        summary["images"] += 1
        _link_one_image(img, caption, url, book, full, surname, grok_client, summary)


def _is_people_photo(caption: str, url: str, grok_client: Any) -> bool:
    """Gate: is this a people photograph (not a map/terrain/diagram)? Uses ImageCaptioner vision
    classification when a client is available; otherwise falls back to the caption (presence of
    parsed names is itself evidence). Fail-safe -> True (caption-driven) on any vision error.
    """
    if grok_client is None:
        return True
    try:
        from src.extraction.image_captioner import ImageCaptioner

        cap = ImageCaptioner(grok_client=grok_client).caption(
            None, {"alt_text": caption, "book": ""}
        )
        cls = (getattr(cap, "classification", "") or "").lower()
        # Only reject when vision is CONFIDENT it's a non-people asset.
        return cls not in ("map", "diagram")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV image_person.vision_error reason=%s (fail-safe=people)",
            str(e)[:120],
        )
        return True


def _link_one_image(
    img: Dict[str, Any],
    caption: str,
    url: str,
    book: str,
    full: Dict[str, Path],
    surname: Dict[str, Path],
    grok_client: Any,
    summary: Dict[str, int],
) -> None:
    """Resolve depicted names in one image's caption and link the image into each person. Fail-safe."""
    try:
        names = parse_caption_person_names(caption)
        if not names:
            logger.info("OBSERV image_person.no_names url=%s", url)
            return
        if not _is_people_photo(caption, url, grok_client):
            summary["skipped"] += 1
            logger.info(
                "OBSERV image_person.skipped reason=not_people_photo url=%s", url
            )
            return
        summary["people_photos"] += 1
        summary["names"] += len(names)
        for name in names:
            pf = _resolve_person_file(name, full, surname)
            if pf is None:
                summary["skipped"] += 1
                logger.info(
                    "OBSERV image_person.unresolved name=%s book=%s", name, book
                )
                continue
            if _append_person_image(pf, url, caption, book):
                summary["linked"] += 1
            else:
                summary["skipped"] += 1
    except Exception as e:  # noqa: BLE001 - linking must never crash the pipeline
        logger.warning("OBSERV image_person.error reason=%s", str(e)[:120])


def _append_person_image(person_file: Path, url: str, caption: str, book: str) -> bool:
    """Append a document-sourced image entry to a person's images[] (schema-valid {url,title,source};
    dedup by url) via the central guarded writer. Fail-safe; returns True iff written.
    """
    try:
        rec = json.loads(person_file.read_text(encoding="utf-8"))
        if not isinstance(rec, dict):
            return False
        images = rec.get("images")
        if not isinstance(images, list):
            images = []
        if any(isinstance(e, dict) and e.get("url") == url for e in images):
            return False  # already linked (dedup by url)
        images.append(
            {
                "url": url,
                "title": caption[:300],
                "source": f"document:{book}" if book else "document",
            }
        )
        rec["images"] = images
        from src.utils.file_lock import write_json_with_lock

        if not write_json_with_lock(person_file, rec, entity="people"):
            logger.warning(
                "OBSERV image_person.write_blocked person=%s — guard refused",
                person_file.name,
            )
            return False
        logger.info(
            "OBSERV image_person.linked person=%s book=%s", person_file.stem, book
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "OBSERV image_person.error person=%s reason=%s",
            person_file.name,
            str(e)[:120],
        )
        return False
