"""Bibliography management — deduplicated document/book reference storage."""

import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid

logger = logging.getLogger(__name__)

# Citation fragments that are cross-references / stubs, not real document references —
# never worth a bibliography record on their own (they point AT another note).
_STUB_RE = re.compile(
    r"^\s*(ibid\.?|op\.?\s*cit\.?|loc\.?\s*cit\.?|see\s+(above|below|n\.?\s*\d+)"
    r"|cited\s+(in\s+)?n\.?\s*\d+)(\s*,?\s*(above|below))?\s*\.?\s*$",
    re.IGNORECASE,
)


def _is_stub_citation(text: str) -> bool:
    """A citation fragment that is a bare cross-reference/stub (e.g. 'Ibid.',
    'See n. 5, above.') carries no standalone document identity — skip it."""
    t = (text or "").strip()
    if len(t) < 4:
        return True
    return bool(_STUB_RE.match(t))


def extract_bibliography_deterministic(
    parsed_file: Path,
    output_root: Path,
    book: str,
    chapter: str,
) -> int:
    """PRIMARY, deterministic bibliography pass — NO LLM / batch / cache.

    Reads the Phase-1-parsed footnotes/endnotes (``*-parsed.json`` / ``*-endnotes.json``,
    each carrying ``{number, url, text}`` captured deterministically by
    ``src.parser.extract_footnotes``) and writes ONE bibliography-candidate record per
    distinct citation via :func:`store_bibliography_entry`. The record's
    ``verbatim_reference`` is the parsed citation text and ``resource_urls`` is the source
    URL — real, citable provenance that EXISTS the moment Phase 1 parses the chapter,
    independent of the fragile event→supplemental→batch→cache→retrieve chain.

    The LLM supplemental step is now an ENRICHMENT OVERLAY (parse author/title/publisher,
    classify document-vs-factual) on these existing records, not their creator — so a
    missed/failed LLM pass degrades gracefully instead of yielding zero bibliography.

    Idempotent: :func:`store_bibliography_entry` dedups by title/verbatim, so re-running
    (submit pass, retrieve re-run, reconcile) only appends mentions, never duplicates.

    Returns the number of citations stored (new or mention-appended).
    """
    notes = _collect_parsed_notes(parsed_file)
    if notes is None:
        return 0
    bib_dir = output_root / "bibliography"
    stored, skipped_stub = _store_notes(notes, bib_dir, book, chapter)
    try:
        logger.info(
            "OBSERV bib.deterministic %s: notes=%d stored=%d skipped_stub=%d",
            parsed_file.name,
            len(notes),
            stored,
            skipped_stub,
        )
    except Exception:  # noqa: BLE001 - observability never breaks the pass
        pass
    return stored


def materialize_bibliography_to_local(output_root: Path) -> int:
    """Mirror Dynamo-backed bibliography entries to local ``output/bibliography/*.json``
    so the Phase-2 S3 sync persists them to the DURABLE system of record (S3 is
    authoritative; DynamoDB is a derived, rebuildable index). In local-file mode (no
    entity store) this is a no-op — the records are already on disk. Fail-safe; returns
    the number of files written.

    Rationale (owner): S3 is the definitive store and can rebuild Dynamo; the reverse
    is not guaranteed and Dynamo is costlier. The deterministic pass must therefore make
    bibliography durable in S3 within Phase 2, independent of the later dedup-phase
    materialization (an interrupted run must not lose records from the durable store).
    """
    try:
        from src.utils.entity_store import get_entity_store

        store = get_entity_store()
    except Exception:  # noqa: BLE001 - import/availability guard
        store = None
    if store is None:
        return 0  # local-file mode: entries are already written to disk by the store
    bib_dir = output_root / "bibliography"
    bib_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        from src.utils.file_lock import write_json_with_lock

        for item in store.list_all("bibliography"):
            entry = item.get("data") or {}
            fname = (
                item.get("filename") or f"{entry.get('BibliographyID', 'unknown')}.json"
            )
            try:
                if write_json_with_lock(bib_dir / fname, entry, entity="bibliography"):
                    written += 1
            except OSError as e:
                logger.warning("materialize bibliography %s failed: %s", fname, e)
    except Exception as e:  # noqa: BLE001 - never break the pass on a store read error
        logger.warning("materialize_bibliography_to_local failed: %s", e)
    logger.info("OBSERV bib.materialized local_files=%d (for S3 durability)", written)
    return written


def _collect_parsed_notes(parsed_file: Path) -> Optional[List[Dict[str, Any]]]:
    """Read the parsed file and return tagged footnote/endnote dicts, or ``None`` if the
    file is absent/unreadable (so the caller returns 0 without raising)."""
    if not parsed_file or not parsed_file.exists():
        return None
    try:
        data = json.loads(parsed_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # malformed parse must not break the pass
        logger.warning("Deterministic bibliography: unreadable %s: %s", parsed_file, e)
        return None
    # Accept either key — footnotes (same-page) or endnotes (end-of-chapter).
    notes: List[Dict[str, Any]] = []
    for key, ref_type in (("footnotes", "footnote"), ("endnotes", "endnote")):
        for n in data.get(key, []) or []:
            if isinstance(n, dict):
                notes.append({**n, "_ref_type": ref_type})
    return notes


def _store_notes(
    notes: List[Dict[str, Any]], bib_dir: Path, book: str, chapter: str
) -> tuple:
    """Store a bibliography record per usable note. Returns (stored, skipped_stub)."""
    stored = 0
    skipped_stub = 0
    for note in notes:
        material = _note_to_material(note)
        if material is None:  # empty text or cross-reference stub
            skipped_stub += 1
            continue
        try:
            if store_bibliography_entry(bib_dir, material, book=book, chapter=chapter):
                stored += 1
        except (
            Exception
        ) as e:  # noqa: BLE001 - one bad citation must not abort the pass
            logger.warning(
                "Deterministic bibliography: store failed for %s fn%s: %s",
                chapter,
                note.get("number"),
                e,
            )
    return stored, skipped_stub


def _note_to_material(note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a deterministic bibliography material from a parsed footnote/endnote.

    Returns ``None`` when the note has no usable text or is a bare cross-reference stub
    (``ibid``/``see n. N``) that carries no standalone document identity.
    """
    text = (note.get("text") or "").strip()
    if not text or _is_stub_citation(text):
        return None
    # Defense-in-depth: only keep http(s) source URLs — a crafted source doc could inject
    # javascript:/data:/file: URLs that would surface downstream in UI/export.
    url = note.get("url") or ""
    if not (isinstance(url, str) and url.lower().startswith(("http://", "https://"))):
        url = ""
    number = note.get("number")
    material = {
        "verbatim_reference": text,
        "reference_type": note.get("_ref_type", "footnote"),
        "reference_number": str(number) if number is not None else "",
        "content_class": "document_reference",
        # Title/author left for the LLM enrichment overlay; store_bibliography_entry
        # falls back to verbatim_reference for the title/dedup key when title=Unknown.
        "citation": {"title": "Unknown"},
        "resource_urls": [url] if url else [],
    }
    # DETERMINISTIC license: anything sourced from ibiblio/hyperwar (the US Army CMH
    # "green books" and material hosted there) is a US Government work -> PUBLIC DOMAIN.
    # Knowable at parse time from the source domain, so set it here (not deferred to the
    # Phase-3 resolver / LLM).
    material.update(_public_domain_fields(url))
    return material


# ibiblio hyperwar hosts the US Army Center of Military History "green books" (US Gov
# works) + related public-domain material. A citation sourced from there is public domain.
_PUBLIC_DOMAIN_HOSTS = ("ibiblio.org/hyperwar", "hyperwar")


def _public_domain_fields(url: str) -> Dict[str, Any]:
    """Deterministic public-domain license fields for a US-Gov/ibiblio-hyperwar source
    URL; empty dict otherwise (left for Phase-3 resolution / LLM)."""
    u = (url or "").lower()
    if not any(h in u for h in _PUBLIC_DOMAIN_HOSTS):
        return {}
    return {
        "license": "public_domain",
        "license_notes": "US Government work (US Army CMH / ibiblio hyperwar); public domain.",
        "copyright_status": {
            "status": "public_domain",
            "determination_basis": "US Government work hosted on ibiblio hyperwar (CMH green books).",
            "jurisdiction": "US",
        },
    }


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
        "book": book,
        "chapter": chapter,
        "reference_type": material.get("reference_type", ""),
        "reference_number": material.get("reference_number", ""),
        "verbatim_reference": material.get("verbatim_reference", ""),
    }
    # EventID/Sub-eventID are NON-nullable ULIDs but OPTIONAL in the mention schema. Only
    # attach them when the material carries a real 26-char ULID — never "" (which would fail
    # the pattern and block the whole bibliography write).
    import re as _re

    _ULID = r"^[0-9A-HJKMNP-TV-Z]{26}$"
    for idf in ("EventID", "Sub-eventID"):
        val = material.get(idf)
        if isinstance(val, str) and _re.match(_ULID, val):
            mention[idf] = val
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
        "copyright_status": material.get("copyright_status"),
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
            from src.utils.file_lock import write_json_with_lock

            write_json_with_lock(
                bib_dir / existing_file, bib_data, entity="bibliography"
            )
        return bib_data.get("BibliographyID")

    bib_data = _build_bib_entry(material)
    bib_data["mentions"].append(mention)
    filename = f"{_slugify(title)}_{bib_data['BibliographyID']}.json"
    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(bib_dir / filename, bib_data, entity="bibliography")
    _index_entry(index, keys, filename)
    _save_index(bib_dir, index)
    logger.debug("New bibliography entry: %s", filename)
    return bib_data["BibliographyID"]
