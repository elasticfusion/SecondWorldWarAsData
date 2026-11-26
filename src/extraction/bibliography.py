"""Bibliography management — deduplicated document/book reference storage."""

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid

from src.schemas import ULID_REGEX
from src.utils.entity_records import citation_identity, merge_mentions

logger = logging.getLogger(__name__)

# Memoized per-process writer id (ECS task id or per-process ULID) used as the bibliography
# shard-filename suffix so concurrent tasks never clobber each other on the shared S3 prefix.
_WRITER_ID: Optional[str] = None

# Citation fragments that are cross-references / stubs, not real document references —
# never worth a bibliography record on their own (they point AT another note).
_STUB_RE = re.compile(
    r"^\s*(ibid\.?|op\.?\s*cit\.?|loc\.?\s*cit\.?|idem|id\.|supra|passim|q\.?\s*v\.?"
    r"|ante|see\s+(above|below|n\.?\s*\d+)"
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


# `ibid`/`op. cit.` WITH trailing content (e.g. "Ibid., p. 47." or "Hodges, op. cit., 112.")
# are NOT bare stubs — they point back to a fuller citation EARLIER IN THE SAME CHAPTER
# (per the corpus rule: a shortened reference always refers to something in that chapter).
_IBID_CONTENT_RE = re.compile(r"^\s*ibid\.?\s*[,.]", re.IGNORECASE)
_OPCIT_CONTENT_RE = re.compile(
    r"^\s*(?P<author>.+?),\s*op\.?\s*cit\.?\b", re.IGNORECASE
)


def _short_title(verbatim: str) -> str:
    """The leading short-title segment of a citation (text before the first comma or
    semicolon), normalized for matching. 'Irwin Diary; Ltr, ...' -> 'irwin diary'."""
    head = re.split(r"[;,]", (verbatim or "").strip(), maxsplit=1)[0]
    return _normalize_title(head)


def _resolve_back_reference(
    verbatim: str, works_so_far: List[Dict[str, str]]
) -> Optional[str]:
    """If ``verbatim`` is a chapter-local back-reference, return the TITLE of the
    antecedent full citation it points to (so title-dedup merges it as a mention);
    otherwise None. ``works_so_far`` is the ordered list of full citations already seen in
    THIS chapter, each ``{'title':..., 'short':..., 'author':...}``.
    """
    if not works_so_far:
        return None
    t = (verbatim or "").strip()

    # 1. "Ibid., p. 47." -> the immediately preceding full citation in this chapter.
    if _IBID_CONTENT_RE.match(t):
        return works_so_far[-1]["title"]

    # 2. "Hodges, op. cit., 112." -> most recent full citation by that author.
    m = _OPCIT_CONTENT_RE.match(t)
    if m:
        return _resolve_opcit(m.group("author"), works_so_far)

    # 3. Short-title repeat (e.g. "Irwin Diary.") -> an earlier full citation whose
    #    short-title matches. Only when the repeat is itself short (a single segment);
    #    a long multi-clause citation is a new work, not a repeat.
    return _resolve_short_title_repeat(t, works_so_far)


def _resolve_opcit(
    author_raw: str, works_so_far: List[Dict[str, str]]
) -> Optional[str]:
    """Resolve an 'op. cit.' back-reference to the most recent work by that author."""
    author = _normalize_title(author_raw)
    for w in reversed(works_so_far):
        if author and (author in w["author"] or author in w["short"]):
            return w["title"]
    return works_so_far[-1]["title"]  # fallback: previous work


def _resolve_short_title_repeat(
    t: str, works_so_far: List[Dict[str, str]]
) -> Optional[str]:
    """Resolve a single-segment short-title repeat to the earlier full citation it matches."""
    norm = _short_title(t)
    if norm and _normalize_title(t) == norm:  # single-segment => candidate repeat
        for w in works_so_far:
            if w["short"] and w["short"] == norm:
                return w["title"]
    return None


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
    notes = collect_parsed_notes(parsed_file)
    if notes is None:
        return 0
    bib_dir = output_root / "bibliography"
    stored, skipped_stub = _store_notes(notes, bib_dir, book, chapter)
    try:
        logger.info(
            "OBSERV bib.deterministic file=%s notes=%d stored=%d skipped_stub=%d",
            parsed_file.name,
            len(notes),
            stored,
            skipped_stub,
        )
    except Exception:  # noqa: BLE001 - observability never breaks the pass
        pass
    return stored


def materialize_bibliography_to_local(output_root: Path) -> int:
    """No-op (DynamoDB entity store removed). The deterministic pass + store_bibliography_entry
    now write ``output/bibliography/*.json`` directly to the S3-authoritative store, so there
    is nothing to mirror from Dynamo. Retained so the Phase-2 call site is undisturbed. Returns 0.
    """
    return 0


def collect_parsed_notes(parsed_file: Path) -> Optional[List[Dict[str, Any]]]:
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
                # Phase 1 records the SOURCE's own label per note (src/notes); the array key is
                # only the fallback for records written before notes carried a kind.
                kind = n.get("kind")
                notes.append(
                    {
                        **n,
                        "_ref_type": (
                            kind if kind in ("footnote", "endnote") else ref_type
                        ),
                    }
                )
    return notes


# Multi-document citation delimiter. GREEN BOOKS separate distinct documents in a single
# footnote with a SEMICOLON; the COMMA is intra-citation there ("Thorson, former G-3, FUSA,
# 12 Sep 56") and must NOT split. This is corpus-specific: OTHER source families may delimit
# differently (a comma CAN be a document separator elsewhere), so the delimiter is a
# parameter, not a baked-in universal rule — keep green-book semantics as the default and
# override per corpus when that work is done.
_GREENBOOK_DOC_DELIMITER = ";"


def _expand_multidoc_notes(
    notes: List[Dict[str, Any]],
    delimiter: str = _GREENBOOK_DOC_DELIMITER,
) -> List[Dict[str, Any]]:
    """Split notes that cite MULTIPLE documents into one sub-note per document.

    ``delimiter`` defaults to the green-book semicolon (safe: comma is intra-citation in
    that corpus). Other corpora may pass a different delimiter — do NOT assume comma splits
    universally. Single-document notes pass through unchanged. Sub-notes keep the same
    number/url/ref_type; reference_number is suffixed (e.g. "3", "3b", "3c") so each is a
    distinct mention.
    """
    out: List[Dict[str, Any]] = []
    for note in notes:
        text = (note.get("text") or "").strip()
        parts = [p.strip() for p in text.split(delimiter) if p.strip()]
        if len(parts) <= 1:
            out.append(note)
            continue
        base_num = note.get("number")
        for i, part in enumerate(parts):
            sub = dict(note)
            sub["text"] = part if part.endswith(".") else part + "."
            if i > 0 and base_num is not None:
                # disambiguate the sub-document so it is a separate mention/record
                sub["number"] = f"{base_num}{chr(ord('b') + i - 1)}"
            out.append(sub)
    return out


def _store_notes(
    notes: List[Dict[str, Any]], bib_dir: Path, book: str, chapter: str
) -> tuple:
    """Store a bibliography record per usable note. Returns (stored, skipped_stub).

    Sequence-aware within the chapter: a back-reference (ibid/op.cit. with trailing
    content, or a short-title repeat like a second 'Irwin Diary.') is resolved to the
    TITLE of its antecedent full citation earlier in THIS chapter and stored under that
    title, so the existing title-dedup merges it as another mention instead of creating a
    duplicate, unresolvable record. (Per the corpus rule: a shortened reference always
    points to something in the same chapter.)
    """
    stored = 0
    skipped_stub = 0
    works_so_far: List[Dict[str, str]] = []
    for note in _expand_multidoc_notes(notes):
        material = _note_to_material(note)
        if material is None:  # empty text or bare cross-reference stub
            skipped_stub += 1
            continue
        verbatim = material.get("verbatim_reference", "")
        antecedent_title = _resolve_back_reference(verbatim, works_so_far)
        if antecedent_title:
            # Back-reference: store under the antecedent's title so dedup merges the mention.
            material.setdefault("citation", {})["title"] = antecedent_title
        else:
            # New full citation: remember it for later back-references in this chapter.
            works_so_far.append(
                {
                    "title": verbatim,  # title falls back to verbatim in store_*
                    "short": _short_title(verbatim),
                    "author": _normalize_title(
                        (material.get("citation") or {}).get("author") or ""
                    ),
                }
            )
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
        # Title + alt_title are BOTH left for Grok (the LLM enrichment step): per the
        # agreed design, alt_title is SEMANTIC (expand green-book shorthand -> full,
        # searchable form, resolve names where known) and is therefore Grok's job, NOT a
        # deterministic derivation. The dedup/resolver key falls back to the MENTION
        # verbatim (always present), so a null alt_title here is safe.
        "citation": {"title": "Unknown", "alt_title": None},
        # SOURCE URL = provenance (where the citation TEXT appeared, e.g. the ibiblio
        # hyperwar page) -> carried on the mention. Deliberately NOT `resource_urls`:
        # resource_urls means "where the CITED document is obtainable" (NARA/archive.org),
        # filled by Phase-3 resolution. Putting the source URL in resource_urls would trip
        # the resolver's "has resource_urls -> already resolved" short-circuit and PREVENT
        # NARA resolution. So resource_urls starts EMPTY.
        "source_url": url,
        "resource_urls": [],
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


# NOTE: alt_title is intentionally NOT derived deterministically. It is a SEMANTIC field
# (expand green-book shorthand -> full searchable title, resolve names) and is set by Grok
# in the Phase-3 enrichment step (_enrich_bib_file). The deterministic pass leaves it None.


def _deterministic_bib_filename(keys: Dict[str, str]) -> str:
    """Stable CANONICAL per-citation filename derived from the match key: ``bib_<16-hex>.json``.
    The same citation always maps to the same canonical name regardless of which Fargate task
    produced it. This is the name RECONCILE collapses to; concurrent WRITERS use
    ``_bib_shard_filename`` (a unique shard) to avoid clobbering each other, and reconcile folds
    the shards into this canonical file. Primary key is the normalized title (= verbatim when
    title='Unknown'), then archive-ref, then author."""
    return f"bib_{_bib_hash(keys)}.json"


def _bib_hash(keys: Dict[str, str]) -> str:
    """16-hex grouping hash for a citation's match key (title -> ref -> author)."""
    primary = keys.get("title") or keys.get("ref") or keys.get("author") or ""
    return hashlib.sha256(primary.encode("utf-8")).hexdigest()[:16]


def _bib_shard_filename(keys: Dict[str, str]) -> str:
    """Unique per-WRITER shard filename ``bib_<hash>__<writer_id>.json``. The ``<hash>`` groups
    the shard with every other write of the same citation; the ``<writer_id>`` suffix is stable
    for the lifetime of THIS task/process (ECS task id, else a per-process ULID) so two
    concurrent Fargate tasks — which share the single ``output/bibliography/`` S3 prefix — NEVER
    write the same key (an additive last-writer-wins S3 sync cannot drop a mention), while
    repeated writes of the SAME citation WITHIN one task resolve to the SAME shard and union in
    place. ``reconcile_bibliography_dir`` later groups shards by ``<hash>`` and collapses them to
    the canonical ``bib_<hash>.json``."""
    return f"bib_{_bib_hash(keys)}__{_writer_id()}.json"


def _writer_id() -> str:
    """Stable identifier for the current writer (task/process). Memoized so every write in this
    process shares one shard suffix. Prefers the Fargate ECS task id; falls back to a ULID
    generated once per process."""
    global _WRITER_ID
    if _WRITER_ID is None:
        _WRITER_ID = os.environ.get("ECS_TASK_ID") or str(ulid.new())
    return _WRITER_ID


def _hash_from_bib_filename(name: str) -> Optional[str]:
    """Extract the grouping ``<hash>`` from a canonical ``bib_<hash>.json`` or a shard
    ``bib_<hash>__<ulid>.json`` filename. Returns None for non-conforming names (e.g. legacy
    random-ULID files), which reconcile then groups by recomputed match key instead."""
    m = re.match(r"^bib_([0-9a-f]{16})(?:__[^.]+)?\.json$", name)
    return m.group(1) if m else None


def _slugify(title: str) -> str:
    """Convert title to filename slug."""
    slug = re.sub(r"[^\w\s-]", "", title.lower().strip())
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug[:80]


def _normalize_title(title: str) -> str:
    """Normalize title for matching: the citation's DOCUMENT identity (``_document_key``),
    lower-cased with punctuation runs collapsed."""
    title = _document_key(title).lower().strip()
    title = re.sub(r"[,.:;'\"\-]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


# --- citation -> document identity -----------------------------------------------------------
# Owner decision (2026-11-07): "116th Inf S-3 Jnl should be one record, with multiple citations".
# A periodic record (unit journal, German KTB war diary, log) is ONE document; the date of the
# entry cited ("6 Jun 44", "27 Jan and 9 Jun 44") belongs to the CITATION (the mention keeps the
# full verbatim), not to the document's identity. For other documents (an interview "31 May 47")
# the date IS identity and is kept. Pages ("p. 81", "pp. 275ff.") are always citation detail,
# and "See" / "cf." are citing cues.
_CITE_CUE = re.compile(r"^(?:see\s+also|see|cf\.?|also)\s+", re.IGNORECASE)
_PAGES = re.compile(
    r"[,;]?\s{0,3}\bpp?\.\s{0,3}[0-9ivxlc]{1,8}(?:\s{0,3}[-–]\s{0,3}[0-9]{1,6})?(?:\s{0,3}ff)?"
    r"\.?\s{0,3}$",
    re.IGNORECASE,
)
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])\.?"
)  # explicit names: "Marine", "Junction", "Decoy" are not months
_ORD = r"(?:st|nd|rd|th)?"
# "6 Jun", "6th June", "6-7 Jun" (a day range)
_DAY_MONTH = (
    rf"\d{{1,2}}{_ORD}(?:\s{{0,3}}[-–]\s{{0,3}}\d{{1,2}}{_ORD})?\s{{1,3}}{_MONTH}"
)
# separators between dated entries: "27 Jan and 9 Jun", "6, 7 Jun", "30 May-1 Jun"
_DATE_SEP = r"(?:\s{0,3}[,–-]\s{0,3}|\s{1,3}and\s{1,3})"
_ENTRY_DATE = re.compile(
    # (?<![^\s,;]): the date starts after whitespace / a separator, never inside an identifier
    # ("KTB 1.I.-30.VI.44, 6 Jun 44": the "44" of the period is not a day of the entry date).
    # The year is optional ("Jnl, 6 Jun").
    rf"[,;]?\s{{0,3}}(?<![^\s,;])(?:\d{{1,2}}{_ORD}{_DATE_SEP}){{0,3}}{_DAY_MONTH}"
    rf"(?:{_DATE_SEP}{_DAY_MONTH}){{0,10}}(?:\s{{1,3}}\d{{1,4}})?[.,;]?\s{{0,3}}$",
    re.IGNORECASE,
)
# US order: "June 6, 1944", "Jun 6th"
_ENTRY_DATE_US = re.compile(
    rf"[,;]?\s{{0,3}}(?<![^\s,;]){_MONTH}\s{{1,3}}\d{{1,2}}{_ORD}"
    rf"(?:\s{{0,3}},?\s{{0,3}}\d{{2,4}})?[.,;]?\s{{0,3}}$",
    re.IGNORECASE,
)
# Periodic records whose cited DATE is an entry, not the document's identity. Not the generic
# "journal": "Journal of Modern History, 14 Mar 1944" is an issue whose date identifies it.
_PERIODIC_RECORD = re.compile(r"\b(?:ktb|jnl|diary|tagebuch|log)\b", re.IGNORECASE)
_CITE_NOISE = frozenset({"see", "cf", "also", "the", "a", "an"})


def _document_key(citation_text: str) -> str:
    """The DOCUMENT a citation names: markdown emphasis, citing cues, trailing pages and - for a
    periodic record - the trailing entry date removed. "*Seventh Army, KTB 1.I.-30.VI.44,* 6 Jun
    44." -> "Seventh Army, KTB 1.I.-30.VI.44"; "See First Army G-3 Jnl." -> "First Army G-3 Jnl".
    """
    text = (citation_text or "").replace("*", " ")
    text = _CITE_CUE.sub("", re.sub(r"\s+", " ", text).strip())
    for _ in range(3):  # "KTB ..., 6 Jun 44, p. 3": strip in any order
        before = text
        text = _PAGES.sub("", text).strip()
        if _PERIODIC_RECORD.search(text):
            text = _ENTRY_DATE.sub("", text).strip()
            text = _ENTRY_DATE_US.sub("", text).strip()
        if text == before:
            break
    return text.rstrip(" ,;.") or (citation_text or "")


_VOLUME_WORDS = frozenset(
    {"vol", "volume", "part", "pt", "book", "bk", "no", "series", "annex"}
)
# a volume NUMBER (Roman or Arabic) - "Book of the Dead" has no volume identifier
_VOLUME_NUMBER = re.compile(
    r"(?=[ivxlc\d])(?:c{0,3}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})|\d{1,4}[a-z]{0,2})"
)


def _identifiers(normalized: str) -> set:
    """Document identifiers in a normalized key: the digit-bearing tokens (B-432, S-3, 116th,
    1.I.-30.VI.44, 1939) plus the word after a volume/part marker ("vol iii" -> "vol iii"), so
    Roman-numeral volumes are told apart."""
    tokens = normalized.split()
    ids = {t for t in tokens if any(c.isdigit() for c in t)}
    for word, nxt in zip(tokens, tokens[1:]):
        if word in _VOLUME_WORDS and _VOLUME_NUMBER.fullmatch(nxt):
            ids.add(f"{word} {nxt}")
    return ids


def _same_document(a: str, b: str) -> bool:
    """Whether two normalized citation keys name the same document. Fuzzy similarity alone
    merged "*Seventh Army*, KTB ..." into "*Fifteenth Army*, KTB ..." (0.897) and would merge
    MS # B-432 / B-433. So, beyond >=0.85 similarity: the identifying START must match (the owner's
    start-of-name rule, src/utils/name_start) and the document identifiers must be IDENTICAL (a
    different manuscript / journal / volume number is a different document; "Vol III" and an
    untagged title are not assumed to be the same work)."""
    from difflib import SequenceMatcher

    from src.utils.name_start import starts_match

    if a == b:
        return True
    if not starts_match(a, b, _CITE_NOISE):
        return False
    if _identifiers(a) != _identifiers(b):
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.85


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

    titles = index.get("titles", {})
    norm = keys.get("title", "")
    if norm and norm in titles:
        return titles[norm]
    if norm:
        for existing_title, ref in titles.items():
            if _same_document(norm, existing_title):
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
    from src.schemas import ULID_PATTERN as _ULID  # single ULID source

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
    # Provenance: the SOURCE URL where the citation TEXT appeared (e.g. the ibiblio
    # hyperwar page). Distinct from the entry's `resource_urls` (where the CITED document
    # is obtainable — filled by Phase-3 resolution). Kept on the mention so it does not
    # trip the resolver's "has resource_urls -> already resolved" short-circuit.
    if material.get("source_url"):
        mention["source_url"] = material["source_url"]
    return mention


def _derive_bib_title(citation: Dict[str, Any], material: Dict[str, Any]) -> str:
    """Best available title for a bibliography record (A000 fix).

    Published works resolve to a real ``citation.title``. UNPUBLISHED archival/military citations
    (interviews, letters, German MS studies, corps memoranda) have no clean title — their identity
    lives in ``alt_title`` or the mention's ``verbatim_reference``. Promote the first non-empty,
    non-"Unknown" of: citation.title -> citation.alt_title -> material.verbatim_reference. Returns
    "Unknown" only when nothing usable exists. Pure/idempotent so it can both build new records and
    backfill existing ones in place.
    """
    for cand in (
        citation.get("title"),
        citation.get("alt_title"),
        material.get("verbatim_reference"),
    ):
        c = str(cand).strip() if cand is not None else ""
        if c and c != "Unknown":
            return c
    return "Unknown"


def _build_bib_entry(material: Dict[str, Any]) -> Dict[str, Any]:
    """Build a new bibliography entry from a material."""
    citation = material.get("citation") or {}
    return {
        "BibliographyID": str(ulid.new()),
        "title": _derive_bib_title(citation, material),
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


def _atomic_write_json(path: str, rec: dict) -> None:
    """Write ``rec`` as JSON to ``path`` atomically (tempfile + os.replace) so an interrupted
    write (e.g. ECS SIGTERM) can never leave a truncated/corrupt record."""
    import tempfile

    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def backfill_bib_titles(bib_dir: Path) -> tuple:
    """Backfill titles on EXISTING bibliography records in place (A000 repair).

    Reuses the exact same :func:`_derive_bib_title` as the write path, so repaired records are
    identical to freshly-extracted ones. For each ``bib_*.json`` whose top-level ``title`` is
    missing/"Unknown", recompute from its ``citation`` + the first mention's ``verbatim_reference``
    (stored records keep the verbatim on the mention). Writes only when the title actually changes.
    Returns ``(scanned, fixed)``. Fail-safe per file.
    """
    import glob as _glob

    scanned = fixed = 0
    for f in _glob.glob(str(bib_dir / "*.json")):
        name = Path(f).name
        if not name.startswith("bib_"):
            continue
        scanned_inc, fixed_inc = _backfill_one_bib_file(f)
        scanned += scanned_inc
        fixed += fixed_inc
    logger.info("OBSERV bib.title_backfill scanned=%d fixed=%d", scanned, fixed)
    return scanned, fixed


def _backfill_one_bib_file(f: str) -> tuple:
    """Backfill the title of one bib record file. Returns (scanned_inc, fixed_inc).

    Mirrors the original inline body exactly: ``scanned`` is counted once the record parses
    as a dict; ``fixed`` only when the title actually changes (and the record is rewritten).
    Fail-safe per file.
    """
    scanned = fixed = 0
    try:
        with open(f, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
        if not isinstance(rec, dict):
            return scanned, fixed
        scanned += 1
        cur = rec.get("title")
        if cur and cur != "Unknown":
            return scanned, fixed  # already has a real title
        fixed += _repair_bib_title(f, rec, cur)
    except Exception as e:  # noqa: BLE001 - per-file best-effort
        logger.debug("bib title backfill skipped %s: %s", f, e)
    return scanned, fixed


def _repair_bib_title(f: str, rec: Dict[str, Any], cur: Any) -> int:
    """Recompute a bib record's title from its citation + first mention; rewrite in place
    when it actually changes. Returns 1 if the record was rewritten, else 0."""
    citation = rec.get("citation") or {}
    mention = (rec.get("mentions") or [{}])[0] or {}
    material = {"verbatim_reference": mention.get("verbatim_reference")}
    new_title = _derive_bib_title(citation, material)
    if new_title and new_title != "Unknown" and new_title != cur:
        rec["title"] = new_title
        if not citation.get("title") or citation.get("title") == "Unknown":
            citation["title"] = new_title
            rec["citation"] = citation
        _atomic_write_json(f, rec)
        return 1
    return 0


def _has_mention(mentions: List[Dict], mention: Dict) -> bool:
    """Check if a mention already exists: the same citation (entity_records.citation_identity -
    same note; an unnumbered note by its quoted text, so distinct unnumbered notes are kept).
    """
    key = citation_identity(mention)
    return any(citation_identity(m) == key for m in mentions if isinstance(m, dict))


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
    title = _derive_bib_title(citation, material)
    mention = _build_mention(material, book, chapter)

    keys = _match_keys(material, title)

    # --- S3-authoritative file write (DynamoDB entity store removed) ---
    bib_dir.mkdir(parents=True, exist_ok=True)
    from src.utils.file_lock import write_json_with_lock

    # CONCURRENT-SAFE SHARDED WRITE (scoped reads): the single ``output/bibliography/`` S3
    # prefix is shared by all concurrently-running per-book Fargate tasks, so a writer must
    # NOT touch a filename another task could also write — an additive last-writer-wins S3
    # sync would otherwise silently drop the loser's mention. Each task therefore writes to
    # its OWN shard ``bib_<hash>__<writer_id>.json`` (writer_id = this task/process). The
    # ``<hash>`` groups the shard with every other write of the same citation; reconcile folds
    # all shards into the canonical ``bib_<hash>.json``, unioning mentions. Because the shard
    # is per-writer-stable, the write path never needs the whole (13k+ file) dir — only its own
    # shard — so Phase 2 can skip the bulk bibliography download. In-task same-citation mentions
    # union into the one local shard here; cross-task union happens in the full-corpus reconcile.
    shard_name = _bib_shard_filename(keys)
    shard_path = bib_dir / shard_name
    if shard_path.exists():
        with open(shard_path, "r", encoding="utf-8") as f:
            bib_data = json.load(f)
        if not _has_mention(bib_data.get("mentions", []), mention):
            bib_data.setdefault("mentions", []).append(mention)
            write_json_with_lock(shard_path, bib_data, entity="bibliography")
        return bib_data.get("BibliographyID")

    bib_data = _build_bib_entry(material)
    bib_data["mentions"].append(mention)
    write_json_with_lock(shard_path, bib_data, entity="bibliography")
    logger.debug("New bibliography shard: %s", shard_name)
    return bib_data["BibliographyID"]


def citation_key(d: Dict[str, Any]) -> str:
    """Dedup key: verbatim text, else alt_title, else author|title (lowercased).

    Deterministic bibliography records (source-url split) store the verbatim on the
    MENTION with top-level verbatim_reference=None and title='Unknown' — so without the
    mention/alt_title fallback EVERY such record keys to '|unknown' and 15/16 wrongly
    collapse in the resolver dedup (only one ever reaches NARA)."""
    cit = d.get("citation") or {}
    verbatim = _stripped_verbatim(d, cit)
    return (
        verbatim
        or str(cit.get("alt_title") or "").strip()
        or f"{cit.get('author', '')}|{cit.get('title', '')}".strip()
    ).lower()


def _stripped_verbatim(d: Dict[str, Any], cit: Dict[str, Any]) -> str:
    """Stripped verbatim text for the dedup key: top-level verbatim_reference / citation.verbatim,
    else the first non-empty mention verbatim_reference, else ""."""
    verbatim = str(d.get("verbatim_reference") or cit.get("verbatim") or "").strip()
    if verbatim:
        return verbatim
    for m in d.get("mentions") or []:
        mv = str(m.get("verbatim_reference") or "").strip()
        if mv:
            return mv
    return ""


def _store_pointers(id_aliases: Any, survivor: str, wanted: List[str]) -> int:
    """Point every id in ``wanted`` at ``survivor``; returns how many failed. Ids with no pointer
    yet go first, so an id absorbed earlier (A -> S1) is checked after its old survivor's own
    pointer (S1 -> survivor) exists and its chain already leads here."""
    end = id_aliases.resolve("bibliography", survivor)
    ends = {o: id_aliases.resolve("bibliography", o) for o in wanted}
    # an id whose chain already ends where the survivor's does needs nothing (a re-run: one read)
    todo = [o for o in wanted if end is None or ends[o] != end]
    fresh = [o for o in todo if ends[o] == o]
    rest = [o for o in todo if o not in fresh]
    failed = 0
    for o in fresh + rest:
        failed += not id_aliases.record("bibliography", o, survivor, "bib_reconcile")
    return failed


def record_merge_pointers(bib_dir: Path) -> bool:
    """Store a pointer (``src/utils/id_aliases``) from every BibliographyID a record absorbed
    (``merged_from``) to that record. Returns False when any could not be stored, or a record could
    not be read (it may be a survivor): then the merged-away files must not be deleted yet (the
    next run combines them again and retries)."""
    from src.utils import id_aliases

    ok = stored = unreadable = 0
    for f in sorted(Path(bib_dir).glob("bib_*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable += 1
            logger.error(
                "OBSERV bib.merge_pointers_unreadable file=%s status=error - a bibliography record "
                "could not be read, so merged-away records are kept this run; fix or remove it",
                f.name,
            )
            continue
        absorbed = rec.get("merged_from") if isinstance(rec, dict) else None
        survivor = str(rec.get("BibliographyID") or "") if isinstance(rec, dict) else ""
        wanted = (
            [str(o) for o in absorbed]
            if isinstance(absorbed, list) and survivor
            else []
        )
        stored += len(wanted)
        ok += len(wanted) - _store_pointers(id_aliases, survivor, wanted)
    logger.info(
        "OBSERV bib.merge_pointers stored=%d failed=%d unreadable=%d",
        ok,
        stored - ok,
        unreadable,
    )
    return ok == stored and not unreadable


def _merge_bib_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge records that share a match key into one: union mentions (entity_records
    .citation_identity), and prefer the most-enriched base fields (a real title over 'Unknown', non-empty
    alt_title/author/resource_urls). Deterministic: records sorted by BibliographyID so the
    outcome is stable regardless of listing order."""
    records = sorted(records, key=lambda r: str(r.get("BibliographyID") or ""))
    base = dict(records[0])
    merged_mentions: List[Dict[str, Any]] = []
    for rec in records:
        _merge_bib_fields(base, rec)
        merge_mentions(merged_mentions, rec.get("mentions"), citation_identity)
    base["mentions"] = merged_mentions
    _note_merged_from(base, records)
    _align_alt_title(base)
    return base


def _note_merged_from(base: Dict[str, Any], records: List[Dict[str, Any]]) -> None:
    """Record on the surviving record every BibliographyID it absorbed (and those they had absorbed),
    so a reference to an absorbed id still finds its record: the merge's provenance. Each citing
    source keeps its own provenance on its mention."""
    own = str(base.get("BibliographyID") or "")
    absorbed = {
        str(i)
        for rec in records
        for i in [rec.get("BibliographyID"), *(rec.get("merged_from") or [])]
        # only a valid ULID can be referenced (the write guard repairs any other primary key)
        if isinstance(i, str) and ULID_REGEX.match(i) and i != own
    }
    if absorbed:
        base["merged_from"] = sorted(absorbed)


def _align_alt_title(rec: Dict[str, Any]) -> None:
    """Make the two copies of alt_title agree (the citation's wins, else the top level's): a
    record whose copies differ is rejected by the write guard, and its merge is lost."""
    raw = rec.get("citation")
    citation: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    if rec.get("alt_title") == citation.get("alt_title"):
        return
    from src.utils.entity_records import set_alt_title

    rec["citation"] = dict(citation)
    set_alt_title(rec, citation.get("alt_title") or rec.get("alt_title"))


def _merge_titles(base: Dict[str, Any], rec: Dict[str, Any]) -> None:
    """A real title over "Unknown", and an alt_title when the base has none."""
    if base.get("title", "Unknown") in ("", "Unknown") and rec.get("title") not in (
        "",
        "Unknown",
        None,
    ):
        base["title"] = rec["title"]
    if not base.get("alt_title") and rec.get("alt_title"):
        from src.utils.entity_records import set_alt_title

        base["citation"] = dict(base.get("citation") or {})  # never alias the shard's
        set_alt_title(base, rec["alt_title"])  # both copies: the guard rejects a drift


def _merge_bib_fields(base: Dict[str, Any], rec: Dict[str, Any]) -> None:
    """Prefer the most-enriched base fields from ``rec``: a real title, an archive reference,
    every copy, and the furthest resolution state."""
    _merge_titles(base, rec)
    if not base.get("archive_reference_number") and rec.get("archive_reference_number"):
        base["archive_reference_number"] = rec["archive_reference_number"]
    _merge_copies(base, rec)
    if _resolution_rank(rec) > _resolution_rank(base):
        # as a unit: a stale field of the other record never mixes in
        base.update({k: rec.get(k) for k in _RESOLUTION_FIELDS})


# how far a record's own search got: a merge keeps the furthest (a resolved record's status,
# source and downloaded file must not be lost under a not-found survivor)
_RESOLUTION_ORDER = (
    None,
    "unresolved",
    "not_found",
    "pending",
    "reference_identified",
    "held_unprocessed",
    "resolved",
)
_RESOLUTION_FIELDS = (
    "search_status",
    "search_source",
    "availability",
    "download_status",
    "download_path",
)


def _resolution_rank(rec: Dict[str, Any]) -> int:
    status = rec.get("search_status")
    return _RESOLUTION_ORDER.index(status) if status in _RESOLUTION_ORDER else 0


def _findings(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    found = (rec.get("source_metadata") or {}).get("findings")
    return [f for f in found if isinstance(f, dict)] if isinstance(found, list) else []


def _merge_copies(base: Dict[str, Any], rec: Dict[str, Any]) -> None:
    """Every copy URL of both records (order kept, no repeats), and both records' approved
    findings, so an approval on either holds for the combined record."""
    urls = list(base.get("resource_urls") or [])
    urls += [u for u in rec.get("resource_urls") or [] if u not in urls]
    if urls:
        base["resource_urls"] = urls
    _merge_findings(base, rec)


def _merge_findings(base: Dict[str, Any], rec: Dict[str, Any]) -> None:
    """Add ``rec``'s findings the base does not hold yet (by URL)."""
    theirs = _findings(rec)
    if not theirs:
        return
    mine = _findings(base)
    seen = {f.get("url") for f in mine}
    meta = dict(base.get("source_metadata") or {})
    meta["findings"] = mine + [f for f in theirs if f.get("url") not in seen]
    base["source_metadata"] = meta


def _group_title(items: List[tuple]) -> str:
    """Representative normalized title for a reconcile group (first record with a real
    title; falls back to verbatim). Empty string when the group is not title-bearing."""
    for _f, data in items:
        title = data.get("title", "") or ""
        if title and title != "Unknown":
            return _normalize_title(title)
    # fall back to a verbatim-derived normalized title
    for _f, data in items:
        verb = data.get("verbatim_reference", "") or (
            (data.get("mentions") or [{}])[0].get("verbatim_reference", "")
        )
        if verb:
            return _normalize_title(verb)
    return ""


# (The near-title automatic merge, _fuzzy_collapse_groups, was removed 2026-11-25: owner D3, exact
# matches only. Its candidates are listed for review by _near_title_candidates instead.)


# a verbatim shorter than this is too generic to say two records cite one work
_MIN_SHARED_VERBATIM = 12


def _citation_text(data: Dict[str, Any]) -> str:
    """The citation's own text (verbatim, else alt_title), lowercased: never the author|title
    fallback ("hodges|unknown" names no work)."""
    raw = data.get("citation")
    cit: Dict[str, Any] = raw if isinstance(raw, dict) else {}
    text = _stripped_verbatim(data, cit) or str(cit.get("alt_title") or "").strip()
    return text.lower()


def _points_elsewhere(plain: str) -> bool:
    """A note that is not a work of its own: too short, a stub or back-reference ("Ibid.", "op.
    cit."), a cross-reference ("See below, ch. III."), or only a date."""
    return bool(
        len(plain) < _MIN_SHARED_VERBATIM
        or _is_stub_citation(plain)
        or _IBID_CONTENT_RE.match(plain)
        or _OPCIT_RE.search(plain)
        or _CROSS_REF_RE.match(plain)
        or not _names_a_work(plain)
    )


def _shared_citation_key(data: Dict[str, Any]) -> str:
    """The citation identity two records must share to be combined, or "" when this record's text
    cannot say which work it cites."""
    key = _citation_text(data)
    plain = re.sub(r"[*_`]+", "", key).strip()  # markdown emphasis hides "*Ibid.*"
    return "" if _points_elsewhere(plain) else key


# a note that points at another note ("See below, ch. III.", "Cf. n. 12"), not at a work
_CROSS_REF_RE = re.compile(
    r"^\s*(see|cf\.?)\s+(also\s+)?(above|below|n\.|nn\.|note|notes|ch\.|chapter|p\.|pp\.)",
    re.IGNORECASE,
)
_OPCIT_RE = re.compile(r"\b(op\.?\s*cit|loc\.?\s*cit)\b", re.IGNORECASE)
_DATE_WORDS = re.compile(rf"\b{_MONTH}|\b\d+(st|nd|rd|th|d)?\b", re.IGNORECASE)


def _names_a_work(text: str) -> bool:
    """Whether the text names something beyond a date (a bare "29 April 1944" is the tail of an
    earlier citation, not a work of its own)."""
    rest = re.sub(r"[^A-Za-z]+", " ", _DATE_WORDS.sub(" ", text))
    return any(len(w) >= 2 for w in rest.split())


def _fold_groups(
    groups: Dict[str, List[tuple]], owner: Dict[str, str], linked: set, target: str
) -> int:
    """Move every group in ``linked`` into ``target`` and re-point the keys they held."""
    folded = 0
    for other in sorted(linked - {target}):
        if other in groups:
            groups[target].extend(groups.pop(other))
            owner.update({k: target for k, g in owner.items() if g == other})
            folded += 1
    return folded


def _verbatim_collapse_groups(groups: Dict[str, List[tuple]]) -> int:
    """Fold groups whose records share a citation identity (the same footnote text) into one,
    mutating ``groups``. Phase 2 can write one footnote as several records (the deterministic pass
    titles it "Unknown", the Grok pass gives it a real title), so their title-based groups differ.
    Groups chained through shared texts all end in the smallest group key (deterministic, one
    pass). Returns the number of groups folded away."""
    owner: Dict[str, str] = {}  # citation key -> the group key that holds it
    folded = 0
    for gkey in sorted(groups):
        if gkey not in groups:
            continue
        keys = {_shared_citation_key(d) for _f, d in groups[gkey]} - {""}
        linked = {owner[k] for k in keys if k in owner} | {gkey}
        target = min(linked)
        folded += _fold_groups(groups, owner, linked, target)
        owner.update({k: target for k in keys})
    return folded


def reconcile_bibliography_dir(
    bib_dir: Path,
    removed: Optional[List[str]] = None,
    held: Optional[List[str]] = None,
) -> int:
    """Rebuild-time reconcile (Option B): union mentions across all bibliography records that
    belong to the same citation, collapsing per-writer shards (``bib_<hash>__<writer>.json``),
    cross-task duplicates, AND legacy random-ULID duplicates into ONE canonical deterministic
    file (``bib_<hash>.json``). Groups primarily by the ``<hash>`` embedded in the filename
    (stable across shards even if one shard's title got enriched differently), falling back to a
    hash recomputed from the record's match key for legacy/non-conforming names; records citing
    the same footnote text are combined (the survivor lists the absorbed ids in ``merged_from``).

    Exact matches only (owner 2026-11-25, D3): groups whose titles are merely SIMILAR are never
    merged here; they are listed in ``merge_review.json`` for a person to approve or deny.
    Every record is brought to the current schema first (D2): one that cannot be used is left out,
    left unchanged and listed in ``held``; one citation that fails to merge keeps its files and
    is said, never stopping the merge of the rest (D1-R1).

    ``removed`` collects the file names merged away, so the caller can delete them where they are
    stored too. Rebuilds the derived index. Idempotent; never raises. Returns the number of
    duplicate files merged away. Run at end-of-run / full-corpus reconcile phase -
    S3-authoritative, no Dynamo."""
    bib_dir = Path(bib_dir)
    if not bib_dir.exists():
        return 0

    # Group record files by citation hash (filename hash preferred; recomputed match-key hash as
    # a legacy fallback); near-title pairs go to review; same-footnote groups are combined.
    groups = _group_bib_files_by_hash(bib_dir, held)
    title_folded = _exact_title_collapse_groups(groups)
    review = _near_title_candidates(groups)
    _write_merge_review(bib_dir, review)
    folded = _verbatim_collapse_groups(groups) + title_folded

    merged_away = 0
    failed = 0
    new_index: Dict[str, Dict[str, str]] = {"titles": {}, "refs": {}, "authors": {}}
    for canon_name, items in groups.items():
        got = _reconcile_bib_group_guarded(
            bib_dir, canon_name, items, new_index, removed
        )
        if got is None:
            failed += 1
        else:
            merged_away += got
    try:
        _save_index(bib_dir, new_index)
    except OSError:
        pass
    logger.info(
        "OBSERV bib.reconcile groups=%d merged_away=%d same_citation_folded=%d "
        "near_title_for_review=%d groups_failed=%d held=%d periodic_date_residue=%d",
        len(groups),
        merged_away,
        folded,
        len(review),
        failed,
        len(held) if held is not None else 0,
        _periodic_date_residue(groups),
    )
    return merged_away


MERGE_REVIEW_FILE = "bibliography_merge_review.json"


def merge_review_path(bib_dir: Path) -> Path:
    """Where the merge candidates for review are written: ``output/review/``, beside (not in) the
    bibliography dir, so nothing that lists the entity dir can take it for a record."""
    return Path(bib_dir).parent / "review" / MERGE_REVIEW_FILE


def _exact_title_collapse_groups(groups: Dict[str, List[tuple]]) -> int:
    """Fold groups whose normalized titles are IDENTICAL into one (the smallest group key),
    mutating ``groups``; returns the number folded away. Identical by ``_group_title``: case,
    punctuation and, for a journal / KTB / diary, the entry date are ignored (the existing
    "one record, many citations" rule; owner to confirm it as an exact rule, TODO D3 rule
    question (a)). Catches a record written under an older file name for the same citation.
    """
    by_title: Dict[str, List[str]] = {}
    for key in sorted(groups):
        title = _group_title(groups[key])
        if title:
            by_title.setdefault(title, []).append(key)
    folded = 0
    for keys in by_title.values():
        for other in keys[1:]:
            groups[keys[0]].extend(groups.pop(other))
            folded += 1
    return folded


def _reconcile_bib_group_guarded(
    bib_dir: Path,
    canon_name: str,
    items: "List[tuple]",
    new_index: "Dict[str, Dict[str, str]]",
    removed: Optional[List[str]],
) -> Optional[int]:
    """``_reconcile_bib_group``, or None when it raised: that citation keeps its files unmerged
    and is said; the merge of every other citation goes on (D1-R1)."""
    from src.utils.observe import observe_error

    try:
        return _reconcile_bib_group(bib_dir, canon_name, items, new_index, removed)
    except (
        Exception
    ) as e:  # noqa: BLE001 - one citation must never stop the corpus merge
        observe_error(
            "bib",
            "group_merge_failed",
            canon=canon_name[:80],
            files=len(items),
            error=type(e).__name__,
            note="this citation's files were not merged and are left as they are; the rest of "
            "the bibliography merged. See the traceback logged with this line, fix the cause, then "
            "re-run the reconcile",
        )
        logger.error(
            "bib group merge traceback canon=%r", canon_name[:80], exc_info=True
        )
        return None


def _near_title_candidates(groups: Dict[str, List[tuple]]) -> List[Dict[str, Any]]:
    """Pairs of groups whose titles name the same document by the near-title rule
    (``_same_document``: same start, same identifiers, similarity >= 0.85) but are not EXACT
    matches. They are NOT merged (owner 2026-11-25, D3): each pair is a candidate for a person to
    approve or deny in the Dedup Review UI. Deterministic order; ``groups`` is not changed.
    """
    titled = sorted(
        (k, t) for k, t in ((k, _group_title(v)) for k, v in groups.items()) if t
    )
    pairs: List[Dict[str, Any]] = []
    for i, (key_i, title_i) in enumerate(titled):
        for key_j, title_j in titled[i + 1 :]:
            if title_i != title_j and _same_document(title_i, title_j):
                pairs.append(_review_pair(groups, key_i, title_i, key_j, title_j))
    return pairs


def _review_pair(groups, key_i, title_i, key_j, title_j) -> Dict[str, Any]:
    """One merge candidate for review: both groups' canonical names, titles and record ids."""

    def ids(key):
        return sorted(
            {str(d.get("BibliographyID") or "") for _f, d in groups[key]} - {""}
        )

    return {
        "rule": "near_title",
        "a": {"canon": key_i, "title": title_i, "ids": ids(key_i)},
        "b": {"canon": key_j, "title": title_j, "ids": ids(key_j)},
    }


def _write_merge_review(bib_dir: Path, pairs: List[Dict[str, Any]]) -> None:
    """Write the merge candidates for human review (the whole list, rebuilt each run)."""
    from src.utils.observe import observe_error

    path = merge_review_path(bib_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"pairs": pairs}, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as e:
        observe_error(
            "bib",
            "merge_review_unwritten",
            error=type(e).__name__,
            note="the near-title merge candidates were not written (nothing was merged): check "
            "the disk, then re-run the reconcile",
        )


_MONTH_WORD = re.compile(rf"\b{_MONTH}", re.IGNORECASE)


def _periodic_date_residue(groups: Dict[str, List[tuple]]) -> int:
    """Groups whose periodic-record key (journal / KTB / diary) still ENDS with a month (within
    its last 4 tokens): an entry date ``_ENTRY_DATE`` did not recognise, so that journal may be
    split across records (the owner's "one record, many citations" rule). A month deep inside a
    long key is a hybrid note's prose (reference + commentary, left to the Grok split), not a
    date residue. >0 -> extend _ENTRY_DATE / _ENTRY_DATE_US for that date form."""
    count = 0
    for items in groups.values():
        key = _group_title(items)
        if not _PERIODIC_RECORD.search(key):
            continue
        if _MONTH_WORD.search(" ".join(key.split()[-4:])):
            count += 1
    return count


def _group_bib_files_by_hash(
    bib_dir: Path, held: Optional[List[str]] = None
) -> "Dict[str, List[tuple]]":
    """Group bibliography record files by their canonical citation filename. Prefer the <hash>
    embedded in the filename (shards + canonical share it); fall back to a hash recomputed from
    the record's match key for legacy random-ULID names. Only usable records are grouped
    (``_usable_bib_record``); the names of those left out are added to ``held``.
    """
    groups: Dict[str, List[tuple]] = {}
    for f in sorted(bib_dir.glob("*.json")):
        data = _usable_bib_record(f, held)
        if data is None:
            continue
        canon = _canonical_bib_name(f, data)
        groups.setdefault(canon, []).append((f, data))
    return groups


def _usable_bib_record(f: Path, held: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    """The record in ``f`` brought to the current schema (src/schemas/schema_contract
    .bring_forward), or None: a non-record file is skipped quietly; an unreadable file or a record
    that cannot be used (wrong shape, needs the upgrade release, newer code) is said, left
    unchanged and listed in ``held`` - kept out of this merge, never silently skipped, and never
    able to stop the merge of the rest (owner 2026-11-25, D1/D2)."""
    from src.schemas.schema_contract import bring_forward, say_unusable
    from src.utils.entity_records import is_record_file
    from src.utils.observe import observe_error

    if not is_record_file(f):
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        observe_error(
            "bib",
            "record_unreadable",
            file=f.name[:120],
            error=type(e).__name__,
            note="the file is not readable JSON: it is left out of the merge and left as it is. "
            "Restore its previous version from S3 (the bucket is versioned) or delete it, then "
            "re-run the reconcile",
        )
        if held is not None:
            held.append(f.name)
        return None
    if not isinstance(data, dict) or "BibliographyID" not in data:
        return None  # not a bibliography record (a sidecar written by another step)
    out = bring_forward(data, "bibliography")
    if not out.usable:
        say_unusable(logger, "bibliography", f.name, out)
        if held is not None:
            held.append(f.name)
        return None
    return out.record


def _canonical_bib_name(f: Path, data: Dict[str, Any]) -> str:
    """Canonical ``bib_<hash>.json`` name for a record: prefer the filename hash; else
    recompute from the record's match key (title, falling back to verbatim)."""
    file_hash = _hash_from_bib_filename(f.name)
    if file_hash:
        return f"bib_{file_hash}.json"
    title = data.get("title", "") or ""
    if not title or title == "Unknown":
        title = data.get("verbatim_reference", "") or (
            (data.get("mentions") or [{}])[0].get("verbatim_reference", "")
        )
    return _deterministic_bib_filename(_match_keys(data, title))


def _reconcile_bib_group(
    bib_dir: Path,
    canon_name: str,
    items: "List[tuple]",
    new_index: "Dict[str, Dict[str, str]]",
    removed: Optional[List[str]] = None,
) -> int:
    """Merge one citation group's records into the canonical file, delete the non-canonical ones,
    and index the result. Returns the count merged away. Fail-safe (never raises)."""
    files = [f for f, _ in items]
    records = [d for _, d in items]
    merged = _merge_bib_records(records) if len(records) > 1 else records[0]
    canon_path = bib_dir / canon_name
    merged_away = _persist_merged_bib(canon_path, canon_name, merged, files, removed)
    if merged_away < 0:
        return 0  # canonical write blocked/failed — shards kept (no data loss)
    for rec in [merged, *records]:
        title = rec.get("title", "") or ""
        if title in (
            "",
            "Unknown",
        ):  # the placeholder names no work: index the text instead
            title = rec.get("verbatim_reference", "") or _citation_text(rec)
        _index_entry(new_index, _match_keys(rec, title), canon_name)
    return merged_away


def _persist_merged_bib(canon_path, canon_name, merged, files, removed=None) -> int:
    """Write the canonical record, then (only on a CONFIRMED write) unlink the non-canonical source
    shards. Returns the count merged away, or -1 if the write was blocked/failed (caller keeps the
    shards — never unlink on an unconfirmed canonical write = no silent data loss). Fail-safe.
    """
    from src.utils.file_lock import write_json_with_lock

    merged_away = 0
    try:
        if not write_json_with_lock(canon_path, merged, entity="bibliography"):
            logger.error(
                "OBSERV bib.reconcile_blocked canon=%s shards=%d — canonical write refused by "
                "schema guard; keeping all source shards (merge aborted, no data deleted)",
                canon_name,
                len(files),
            )
            return -1
        for f in files:
            if f.name != canon_name:
                try:
                    f.unlink()
                    merged_away += 1
                    if removed is not None:
                        removed.append(f.name)
                except OSError:
                    pass
    except Exception as e:  # noqa: BLE001 - reconcile must never break the run
        logger.warning("reconcile bibliography %s failed: %s", canon_name, e)
        return -1
    return merged_away
