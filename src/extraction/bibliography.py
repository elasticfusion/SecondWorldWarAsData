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


def _normalize_ref(ref: str) -> str:
    """Normalize an archive reference number (NARA RG/entry/box etc.) for exact
    matching — strip case, punctuation-runs, and whitespace."""
    ref = (ref or "").lower().strip()
    ref = re.sub(r"[.,;:#]+", " ", ref)
    return re.sub(r"\s+", " ", ref).strip()


def _author_key(citation: Dict[str, Any]) -> str:
    """Build a normalized author key from citation.author (an array of strings)."""
    authors = citation.get("author") or []
    if isinstance(authors, str):
        authors = [authors]
    joined = " ".join(a for a in authors if a)
    joined = re.sub(r"[,.;:'\"\-]+", " ", joined.lower())
    return re.sub(r"\s+", " ", joined).strip()


def _match_keys(material_or_entry: Dict[str, Any], title: str) -> Dict[str, str]:
    """Extract the dedup match keys for a bibliography material/entry, in fallback
    priority: title (primary) -> archive_reference_number -> author (operator spec).
    Returns {'title', 'ref', 'author'} (empty strings when absent)."""
    citation = material_or_entry.get("citation") or {}
    ref = material_or_entry.get("archive_reference_number") or citation.get(
        "archive_reference_number", ""
    )
    return {
        "title": _normalize_title(title),
        "ref": _normalize_ref(ref or ""),
        "author": _author_key(citation),
    }


def _load_index(bib_dir: Path) -> Dict[str, Dict[str, str]]:
    """Load the multi-key bibliography index. Structure:
    {'titles': {norm_title: id}, 'refs': {norm_ref: id}, 'authors': {author: id}}.
    Back-compat: a legacy flat {norm_title: filename} index is read as 'titles'."""
    index_file = bib_dir / "index.json"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if raw and not any(k in raw for k in ("titles", "refs", "authors")):
            return {"titles": raw, "refs": {}, "authors": {}}  # legacy flat
        return {
            "titles": raw.get("titles", {}),
            "refs": raw.get("refs", {}),
            "authors": raw.get("authors", {}),
        }
    return {"titles": {}, "refs": {}, "authors": {}}


def _save_index(bib_dir: Path, index: Dict[str, Dict[str, str]]) -> None:
    """Save the multi-key bibliography index."""
    with open(bib_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False, sort_keys=True)


def _find_match(
    keys: Dict[str, str], index: Dict[str, Dict[str, str]]
) -> Optional[str]:
    """Find an existing entry, title-focused with fallback to archive-ref then
    author (operator spec). Returns the mapped filename/id or None.

    1. TITLE: exact normalized, then 0.85 fuzzy.
    2. ARCHIVE REF: exact normalized match (a NARA reference uniquely identifies a
       source even when the title string differs).
    3. AUTHOR: exact normalized author-key match (weak — same author+work cited
       with a divergent title)."""
    from difflib import SequenceMatcher

    titles = index.get("titles", {})
    norm = keys.get("title", "")
    if norm and norm in titles:
        return titles[norm]
    if norm:
        for existing_title, ref in titles.items():
            if SequenceMatcher(None, norm, existing_title).ratio() >= 0.85:
                return ref
    ref_key = keys.get("ref", "")
    if ref_key and ref_key in index.get("refs", {}):
        return index["refs"][ref_key]
    author = keys.get("author", "")
    if author and author in index.get("authors", {}):
        return index["authors"][author]
    return None


def _index_entry(
    index: Dict[str, Dict[str, str]], keys: Dict[str, str], value: str
) -> None:
    """Register an entry's keys (title/ref/author) -> value in the multi-key index."""
    if keys.get("title"):
        index.setdefault("titles", {})[keys["title"]] = value
    if keys.get("ref"):
        index.setdefault("refs", {})[keys["ref"]] = value
    if keys.get("author"):
        index.setdefault("authors", {})[keys["author"]] = value


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
    """Store a document reference, deduplicating by title.

    Dynamo-backed when the entity store is available (G3 / spec §3.3): the
    title index + entries live in DynamoEntityStore with atomic version-conditional
    writes, so concurrent books adding references are race-safe across Fargate
    hosts (the old flock-guarded S3 JSON writes were per-host = NOT safe) AND
    Phase 2 no longer bulk-downloads the 13,566-file bibliography dir. Falls back
    to the local-file implementation when no store (local/test mode).

    Returns the BibliographyID of the stored/updated entry, or None on error.
    """
    citation = material.get("citation") or {}
    title = citation.get("title", "")
    if not title or title == "Unknown":
        title = material.get("verbatim_reference", "Unknown")
    mention = _build_mention(material, book, chapter)

    store = None
    try:
        from src.utils.entity_store import get_entity_store

        store = get_entity_store()
    except Exception:  # pragma: no cover - import guard
        store = None

    keys = _match_keys(material, title)

    if store is not None:
        new_id = str(ulid.new())

        def _build():
            return _build_bib_entry(material)

        return store.store_bibliography(
            keys=keys,
            bib_id=new_id,
            entry_builder=_build,
            mention=mention,
            mention_exists=lambda mentions: _has_mention(mentions, mention),
        )

    # --- Local-file fallback (no Dynamo store: local/test mode) ---
    bib_dir.mkdir(parents=True, exist_ok=True)
    index = _load_index(bib_dir)
    existing_file = _find_match(keys, index)
    if existing_file and (bib_dir / existing_file).exists():
        with open(bib_dir / existing_file, "r", encoding="utf-8") as f:
            bib_data = json.load(f)
        if not _has_mention(bib_data.get("mentions", []), mention):
            bib_data.setdefault("mentions", []).append(mention)
            with open(bib_dir / existing_file, "w", encoding="utf-8") as f:
                json.dump(bib_data, f, indent=2, ensure_ascii=False)
        return bib_data.get("BibliographyID")

    bib_data = _build_bib_entry(material)
    bib_data["mentions"].append(mention)
    filename = f"{_slugify(title)}_{bib_data['BibliographyID']}.json"
    with open(bib_dir / filename, "w", encoding="utf-8") as f:
        json.dump(bib_data, f, indent=2, ensure_ascii=False)
    _index_entry(index, keys, filename)
    _save_index(bib_dir, index)
    logger.debug("New bibliography entry: %s", filename)
    return bib_data["BibliographyID"]
