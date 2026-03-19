"""Bibliography management — deduplicated document/book reference storage."""

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid

logger = logging.getLogger(__name__)


def _slugify(title: str) -> str:
    """Convert title to filename slug."""
    slug = re.sub(r"[^\w\s-]", "", title.lower().strip())
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug[:80]


def _normalize_title(title: str) -> str:
    """Normalize title for matching."""
    title = title.lower().strip()
    title = re.sub(r"[,.:;'\"\-]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _load_index(bib_dir: Path) -> Dict[str, str]:
    """Load bibliography index mapping normalized titles to filenames."""
    index_file = bib_dir / "index.json"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_index(bib_dir: Path, index: Dict[str, str]) -> None:
    """Save bibliography index."""
    with open(bib_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False, sort_keys=True)


def _find_match(title: str, index: Dict[str, str]) -> Optional[str]:
    """Find existing bibliography entry by title similarity. Returns filename or None."""
    norm = _normalize_title(title)
    if norm in index:
        return index[norm]
    # Fuzzy match for near-duplicates
    for existing_title, filename in index.items():
        if SequenceMatcher(None, norm, existing_title).ratio() >= 0.85:
            return filename
    return None


def _build_mention(
    material: Dict[str, Any],
    book: str,
    chapter: str,
) -> Dict[str, Any]:
    """Build a mention entry from a supplemental material."""
    mention = {
        "MentionID": str(ulid.new()),
        "EventID": material.get("EventID", ""),
        "Sub-eventID": material.get("Sub-eventID", ""),
        "book": book,
        "chapter": chapter,
        "reference_type": material.get("reference_type", ""),
        "reference_number": material.get("reference_number", ""),
        "verbatim_reference": material.get("verbatim_reference", ""),
    }
    # Add page/volume from citation if present
    citation = material.get("citation") or {}
    if citation.get("pages"):
        mention["pages"] = citation["pages"]
    if citation.get("volume"):
        mention["volume"] = citation["volume"]
    return mention


def _build_bib_entry(material: Dict[str, Any]) -> Dict[str, Any]:
    """Build a new bibliography entry from a material."""
    citation = material.get("citation") or {}
    return {
        "BibliographyID": str(ulid.new()),
        "title": citation.get("title", "Unknown"),
        "alt_title": citation.get("alt_title"),
        "citation": citation,
        "availability": material.get("availability", "unknown"),
        "resource_urls": material.get("resource_urls", []),
        "archive_reference_number": material.get("archive_reference_number"),
        "archive_physical_address": material.get("archive_physical_address"),
        "license": material.get("license", "unknown"),
        "license_notes": material.get("license_notes"),
        "mentions": [],
    }


def _has_mention(mentions: List[Dict], mention: Dict) -> bool:
    """Check if a mention already exists (same event + sub-event + reference_number)."""
    for m in mentions:
        if (
            m.get("EventID") == mention.get("EventID")
            and m.get("Sub-eventID") == mention.get("Sub-eventID")
            and m.get("reference_number") == mention.get("reference_number")
        ):
            return True
    return False


def store_bibliography_entry(
    bib_dir: Path,
    material: Dict[str, Any],
    book: str,
    chapter: str,
) -> Optional[str]:
    """Store a document reference in output/bibliography/, deduplicating by title.

    Returns the BibliographyID of the stored/updated entry, or None on error.
    """
    bib_dir.mkdir(parents=True, exist_ok=True)
    citation = material.get("citation") or {}
    title = citation.get("title", "")
    if not title or title == "Unknown":
        title = material.get("verbatim_reference", "Unknown")

    index = _load_index(bib_dir)
    mention = _build_mention(material, book, chapter)

    # Try to find existing entry
    existing_file = _find_match(title, index)
    if existing_file and (bib_dir / existing_file).exists():
        with open(bib_dir / existing_file, "r", encoding="utf-8") as f:
            bib_data = json.load(f)
        if not _has_mention(bib_data.get("mentions", []), mention):
            bib_data.setdefault("mentions", []).append(mention)
            with open(bib_dir / existing_file, "w", encoding="utf-8") as f:
                json.dump(bib_data, f, indent=2, ensure_ascii=False)
        return bib_data.get("BibliographyID")

    # Create new entry
    bib_data = _build_bib_entry(material)
    bib_data["mentions"].append(mention)

    filename = f"{_slugify(title)}_{bib_data['BibliographyID']}.json"
    with open(bib_dir / filename, "w", encoding="utf-8") as f:
        json.dump(bib_data, f, indent=2, ensure_ascii=False)

    index[_normalize_title(title)] = filename
    _save_index(bib_dir, index)
    logger.debug("New bibliography entry: %s", filename)
    return bib_data["BibliographyID"]
