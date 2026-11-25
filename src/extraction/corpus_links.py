"""The citation index of ibiblio HyperWar volumes (owner 2026-11-24).

Every volume will be ingested in the end (owner), in batches the owner approves (Grok cost). Until a
cited volume is in the corpus, its citations wait here, so nothing is lost and the records to update
are known when it arrives.

Stored in the data bucket under ``corpus-links/`` (``output/_corpus_links/`` locally), outside
``output/`` so no sync or trigger touches it, and written per book so concurrent Phase 3 tasks never
write the same file:

* ``volumes/<book>.json`` - the volume a book IS (``{"book", "volume", "source_url"}``): the
  volume -> book table. Book slugs are immutable and need not equal the volume key (``XChannel`` and
  ``CrossChannelAttack`` are both ``USA-E-XChannel``), so volumes are matched by key, never by slug
  (a PDF book with no source URL that is named by its volume key, ``USA-E-XChannel``, counts:
  ``volume_basis: slug``).
* ``citations/<volume>/<book>.json`` - every link from ``<book>`` to ``<volume>``: the citing
  SourceSectionID, chapter, URL and printed page (``to_page``). Rewritten whole on each run of the book
  (its citations are recomputed from its parsed documents), so it is idempotent.
* ``ingest-list.json`` - per cited volume: its index URL, how many links and books cite it, and
  whether it is held (``held_as``: the books that are it) or still ``pending``. The pending ones, most
  cited first, are the volumes to ingest; for a held one the citations are the records to update.

* ``links/<book>.json`` - each of ``<book>``'s citations of a HELD volume connected to that volume's
  defining copy (a PDF first: owner rule): its BookID, edition and chapter (found from the page the
  citing book printed), the page as cited, and the supporting copies. Regenerable: rebuilt whole
  each run; the original records are not touched.

Marking the bibliography entry as in the corpus is still open (TODO, Group 9).
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

S3_PREFIX = "corpus-links"
_LOCAL_DIR = Path("output/_corpus_links")
INGEST_LIST = "ingest-list.json"
_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_CITATIONS = 5000  # per (volume, book): far above any real book


def _store() -> Any:
    from src.utils import find_pages

    return find_pages.data_bucket_storage(S3_PREFIX, _LOCAL_DIR)


def _safe(name: Any) -> str:
    """A name safe as one path segment (volume keys and slugs already are; anything else is
    reduced to the safe characters, never a separator)."""
    return _SAFE.sub("_", str(name or ""))[:120].strip("._") or "_"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


_IBIBLIO_HOSTS = ("www.ibiblio.org", "ibiblio.org")
_MAX_VOLUMES_PER_BOOK = 200


def volume_index_url(url: str, volume: str) -> Optional[str]:
    """``https://www.ibiblio.org/.../<volume>/index.html`` for an ibiblio http(s) URL inside
    ``volume``; None for anything else (a later importer fetches it: only ibiblio, only http(s)).
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    marker = f"/{volume}/"
    i = parts.path.find(marker)
    if (
        parts.scheme not in ("http", "https")
        or parts.hostname not in _IBIBLIO_HOSTS
        or i < 0
    ):
        return None
    return f"https://www.ibiblio.org{parts.path[: i + len(marker)]}index.html"


# a HyperWar volume key spelled as a slug: <country>-<series letter>-<volume> (USA-E-XChannel,
# USA-A-Utah). Used only for a book with no source URL (an uploaded PDF named after its volume)
_SLUG_VOLUME = re.compile(r"^[A-Z]{2,6}-[A-Z]-[A-Za-z0-9]+$")
_PAGE = re.compile(r"page(\d{1,5})$", re.I)


def own_volume(book: str, source_url: str) -> "tuple[Optional[str], str]":
    """(the volume a book is, how that is known): its source URL's volume; else, for a book with no
    source URL, a HyperWar volume-key slug (``USA-E-XChannel``, a trailing chapter number dropped);
    else (None, "unknown"). A slug claim is weaker evidence than a source URL (``volume_basis``).
    """
    from src.extraction.intra_corpus import _volume_of

    vol = _volume_of(source_url)
    if vol:
        return vol, "source_url"
    if source_url:
        return (
            None,
            "unknown",
        )  # a URL that is not a volume page: never overridden by the name
    slug = re.sub(r"-\d+$", "", (book or "")[:200])
    return (slug, "slug") if _SLUG_VOLUME.match(slug) else (None, "unknown")


def source_format(source_key: Optional[str], declared: Optional[str] = None) -> str:
    """``pdf`` / ``html`` / another extension / ``unknown``: what the book's text was made from -
    the uploaded object's extension (Phase 0 records ``source_key``), else what the book's meta
    declares (the HyperWar import writes ``source_format: html``), else ``unknown``. Never inferred
    from a source URL: a PDF of a HyperWar volume carries an ibiblio source URL too."""
    last = (source_key or "").rstrip().rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)[-1].lower() if "." in last else ""
    if ext and re.fullmatch(r"[a-z0-9]{1,10}", ext):
        return "html" if ext in ("htm", "xhtml") else ext
    declared = str(declared or "").strip().lower()
    return declared if re.fullmatch(r"[a-z0-9]{1,10}", declared) else "unknown"


def record_book(
    book: str,
    source_url: str,
    chapters: Iterable[Dict[str, Any]],
    info: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Record what ``book`` is and which other volumes it cites. ``chapters``: for each parsed
    document, ``{"chapter", "chapter_title", "first_page", "last_page", "edges"}`` (edges from
    ``intra_corpus.process_doc_links``). ``info``: the book's ``edition``, ``copyright_date`` and
    ``source_key`` (its chapter meta, carried by the parsed documents). Returns {volumes_cited,
    citations}. A storage write error is said, not raised.
    """
    chapters = list(chapters)
    store = _store()
    own = _write_book(store, book, source_url, chapters, info or {})
    by_volume = _capped(_citations_by_volume(chapters, own), book)
    total = 0
    for vol, cites in by_volume.items():
        total += _write_citations(store, book, vol, cites)
    _drop_stale(store, book, set(by_volume))
    return {"volumes_cited": len(by_volume), "citations": total}


def _write_book(
    store: Any,
    book: str,
    source_url: str,
    chapters: List[Dict[str, Any]],
    info: Dict[str, Any],
) -> Optional[str]:
    """Write ``volumes/<book>.json`` (what the book is); returns its volume."""
    own, basis = own_volume(book, source_url)
    if (
        not own and source_url
    ):  # a book with no source URL is simply not a HyperWar volume
        logger.warning(
            "OBSERV corpus_links.book_volume_unknown book=%r status=warn - the book's source URL "
            "is not an ibiblio HyperWar volume page, so it is not counted as holding any volume "
            "(its own volume may be listed as pending). Check the book's meta.yaml source_url",
            book[:80],
        )
    _write(
        store,
        f"volumes/{_safe(book)}.json",
        {
            "book": book,
            "volume": own,
            "volume_basis": basis,
            "source_url": source_url or None,
            "format": source_format(info.get("source_key"), info.get("source_format")),
            "edition": info.get("edition") or None,
            "copyright_date": info.get("copyright_date") or None,
            "chapters": _chapter_pages(chapters),
            "at": _now(),
        },
    )
    return own


def _capped(
    by_volume: Dict[str, List[Dict[str, Any]]], book: str
) -> Dict[str, List[Dict[str, Any]]]:
    if len(by_volume) <= _MAX_VOLUMES_PER_BOOK:
        return by_volume
    logger.warning(
        "OBSERV corpus_links.too_many_volumes book=%r volumes=%d cap=%d status=warn - only the "
        "most-cited are indexed; check the book's links (a real book cites a few volumes)",
        book[:80],
        len(by_volume),
        _MAX_VOLUMES_PER_BOOK,
    )
    top = sorted(by_volume, key=lambda v: -len(by_volume[v]))[:_MAX_VOLUMES_PER_BOOK]
    return {v: by_volume[v] for v in top}


def _write_citations(
    store: Any, book: str, vol: str, cites: List[Dict[str, Any]]
) -> int:
    """Write ``citations/<vol>/<book>.json``; returns the citations kept."""
    if len(cites) > _MAX_CITATIONS:
        logger.warning(
            "OBSERV corpus_links.citations_capped book=%r volume=%s citations=%d cap=%d "
            "status=warn - only the first are kept; check the book's links to this volume",
            book[:80],
            vol,
            len(cites),
            _MAX_CITATIONS,
        )
    _write(
        store,
        f"citations/{_safe(vol)}/{_safe(book)}.json",
        {
            "volume": vol,
            "volume_index_url": volume_index_url(str(cites[0]["to_url"] or ""), vol),
            "cited_by_book": book,
            "citations": cites[:_MAX_CITATIONS],
            "at": _now(),
        },
    )
    return min(len(cites), _MAX_CITATIONS)


def _citations_by_volume(
    chapters: Iterable[Dict[str, Any]], own: Optional[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """The book's links to OTHER volumes, by volume, once per (chapter, URL, note)."""
    by_volume: Dict[str, Dict[tuple, Dict[str, Any]]] = {}
    for ch in chapters:
        for e in ch.get("edges") or []:
            vol = e.get("to_volume")
            if e.get("kind") != "in_corpus_doc" or not vol or vol == own:
                continue
            cite = {
                "from_SourceSectionID": e.get("from_SourceSectionID"),
                "chapter": ch.get("chapter"),
                "chapter_title": ch.get("chapter_title"),
                "to_url": e.get("to_url"),
                "to_page": e.get("to_page"),
                # the note the link is in (its number, and the parsed chapter file Phase 2 names
                # the note's bibliography mention by); None for a link in the narrative
                "reference_number": e.get("reference_number"),
                "parsed_chapter": e.get("parsed_chapter"),
                "parsed_book": e.get("parsed_book"),
            }
            key = (  # sections 8a/8b share chapter 8 and may repeat note numbers
                cite["chapter"],
                cite["parsed_chapter"],
                cite["to_url"],
                cite["reference_number"],
            )
            by_volume.setdefault(vol, {})[key] = cite
    return {vol: list(cites.values()) for vol, cites in by_volume.items()}


def _chapter_pages(chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each chapter's printed-page range (its sections merged), for finding a cited page."""
    out: Dict[Any, Dict[str, Any]] = {}
    for ch in chapters:
        key = ch.get("chapter")
        if key is None:
            continue
        c = out.setdefault(
            key,
            {"chapter_number": key, "chapter_title": ch.get("chapter_title"),
             "first_page": None, "last_page": None},  # fmt: skip
        )
        for field, pick in (("first_page", min), ("last_page", max)):
            vals = [v for v in (c[field], ch.get(field)) if isinstance(v, int)]
            c[field] = pick(vals) if vals else None
    return list(out.values())


def _drop_stale(store: Any, book: str, cited: set) -> None:
    """Remove this book's citation files for volumes it no longer cites."""
    name = f"{_safe(book)}.json"
    keep = {_safe(v) for v in cited}
    for path in _list(store, "citations"):
        parts = path.split("/")
        if len(parts) >= 2 and parts[-1] == name and parts[-2] not in keep:
            try:
                store.delete(path)
            except Exception as e:  # noqa: BLE001 - said; the list is rebuilt next run
                _write_failed("delete", path, e)


def _list(store: Any, prefix: str) -> List[str]:
    """Every JSON file under ``prefix``, at any depth (S3 lists recursively; a local store needs
    the recursive pattern)."""
    from src.utils.storage import LocalStorage

    pattern = "**/*.json" if isinstance(store, LocalStorage) else "*.json"
    return store.list_files(prefix, pattern)


def _write(store: Any, path: str, data: Dict[str, Any]) -> bool:
    try:
        store.write_json(path, data)
        return True
    except Exception as e:  # noqa: BLE001 - said; rewritten on the book's next run
        _write_failed("write", path, e)
        return False


def _write_failed(what: str, path: str, e: Exception) -> None:
    logger.error(
        "OBSERV corpus_links.%s_failed path=%s err=%s status=error - the citation index is "
        "incomplete until the book's next Phase 3 run. Check the task role's s3:PutObject / "
        "s3:DeleteObject on corpus-links/ in the data bucket",
        what,
        path[:160],
        type(e).__name__,
    )


def _read_all(
    store: Any, prefix: str, failed: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Every JSON record under ``prefix``; an unreadable one is said, left out, and its path added
    to ``failed``."""
    out = []
    for path in _list(store, prefix):
        try:
            data = store.read_json(path)
        except Exception as e:  # noqa: BLE001 - said; that file is left out
            if failed is not None:
                failed.append(path)
            logger.warning(
                "OBSERV corpus_links.unreadable path=%s err=%s status=warn - left out of the "
                "ingestion list; it is rewritten by its book's next Phase 3 run",
                path[:160],
                type(e).__name__,
            )
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def build_ingest_list(
    also_held: Iterable[str] = (), links_out: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Rebuild ``ingest-list.json`` (and the links) from every book's records. ``also_held``:
    volume keys known to be in the corpus from elsewhere (this task's source sections).
    ``links_out`` receives every link built. Returns the list."""
    store = _store()
    failed: List[str] = []  # any unreadable record: no link file is deleted this run
    books = {
        str(r.get("book")): r
        for r in _read_all(store, "volumes", failed)
        if r.get("book")
    }
    citations = _read_all(store, "citations", failed)
    volumes = _cited(citations)
    _annotate(volumes, _held(books.values(), also_held), books)
    write_links(store, citations, volumes, books, partial=bool(failed), out=links_out)
    ordered = sorted(
        volumes.values(), key=lambda v: (v["status"] != "pending", -v["links"])
    )
    pending = sum(1 for v in ordered if v["status"] == "pending")
    result = {
        "built_at": _now(),
        "pending": pending,
        "held": len(ordered) - pending,
        "partial": bool(failed),  # some records could not be read this run
        "volumes": ordered,
    }
    _write(store, INGEST_LIST, result)
    logger.info(
        "OBSERV corpus_links.ingest_list volumes=%d pending=%d held=%d - pending volumes are "
        "cited but not in the corpus: ingest them (owner-approved batches) from corpus-links/%s",
        len(ordered),
        result["pending"],
        result["held"],
        INGEST_LIST,
    )
    return result


def _annotate(
    volumes: Dict[str, Dict[str, Any]],
    held: Dict[str, List[str]],
    books: Dict[str, Dict[str, Any]],
) -> None:
    """Each cited volume's citing books, status, the books holding it and its defining copy."""
    for vol, v in volumes.items():
        holders = sorted(set(held.get(vol, [])))
        v["cited_by"] = sorted({b for b in v["cited_by"] if b})
        v["status"] = "held" if vol in held else "pending"
        v["held_as"] = holders or None
        v["defining_copy"] = defining_copy([books[b] for b in holders if b in books])


def _held(
    records: Iterable[Dict[str, Any]], also_held: Iterable[str]
) -> Dict[str, List[str]]:
    """Volume key -> the books that are it."""
    held: Dict[str, List[str]] = {v: [] for v in also_held if v}
    for rec in records:
        if rec.get("volume"):
            held.setdefault(rec["volume"], []).append(str(rec.get("book")))
    return held


def _cited(citations: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Volume key -> its index URL, link count and citing books, from every book's citations."""
    volumes: Dict[str, Dict[str, Any]] = {}
    for rec in citations:
        vol = rec.get("volume")
        if not vol:
            continue
        v = volumes.setdefault(
            vol, {"volume": vol, "volume_index_url": None, "links": 0, "cited_by": []}
        )
        v["volume_index_url"] = v["volume_index_url"] or rec.get("volume_index_url")
        v["links"] += len(rec.get("citations") or [])
        v["cited_by"].append(rec.get("cited_by_book"))
    return volumes


# --- connecting citations to the copy of the cited volume we hold -------------------------------
#
# Owner rule (2026-11-08, 2026-11-24): the PDF is the defining document; an ibiblio transcription
# supports it. A citation names the cited volume's EDITION and CHAPTER; the page the citing book
# printed is kept as cited (printings usually, not always, share pagination). Where only HTML is
# held, the chapter is found if it can be and that is that.

_FORMAT_RANK = {"pdf": 0, "html": 2, "unknown": 3}  # any other upload format: 1


def defining_copy(copies: List[Dict[str, Any]]) -> Optional[str]:
    """The book that defines a volume: a PDF first, then any other upload, then an HTML
    transcription, then a copy whose format is unknown; among equals one with its edition
    recorded, then the first slug (a stable, written-down tiebreak)."""
    if not copies:
        return None
    best = min(
        copies,
        key=lambda c: (
            _FORMAT_RANK.get(str(c.get("format") or "unknown"), 1),
            not c.get("edition"),
            str(c.get("book")),
        ),
    )
    return str(best.get("book"))


def _cited_page(to_page: Any) -> Optional[int]:
    m = _PAGE.search(str(to_page or "")[:200])
    return int(m.group(1)) if m else None


def _chapters_for(copy: Dict[str, Any], page: Optional[int]) -> List[Dict[str, Any]]:
    """Every chapter of ``copy`` whose printed pages include ``page`` (two when a chapter ends and
    the next begins on that page)."""
    if page is None:
        return []
    return [
        ch
        for ch in copy.get("chapters") or []
        if isinstance(ch.get("first_page"), int)
        and isinstance(ch.get("last_page"), int)
        and ch["first_page"] <= page <= ch["last_page"]
    ]


def link_for(
    citation: Dict[str, Any],
    citing_book: str,
    copy: Dict[str, Any],
    supporting: List[str],
) -> Dict[str, Any]:
    """One citation connected to the defining copy of the volume it cites (provenance: what was
    matched, how, and when)."""
    from src.utils.deterministic_ids import book_id

    page = _cited_page(citation.get("to_page"))
    found = _chapters_for(copy, page)
    chapter = found[0] if len(found) == 1 else None  # two candidates: not guessed
    return {
        "citing": {
            "book": citing_book,
            "SourceSectionID": citation.get("from_SourceSectionID"),
            "chapter": citation.get("chapter"),
            "url": citation.get("to_url"),
            "reference_number": citation.get("reference_number"),
            "parsed_chapter": citation.get("parsed_chapter"),
            "parsed_book": citation.get("parsed_book"),
        },
        "cited": {
            "book": copy.get("book"),
            "BookID": book_id(str(copy.get("book"))),
            "volume": copy.get("volume"),
            "volume_basis": copy.get("volume_basis"),
            "source_url": copy.get("source_url"),
            "format": copy.get("format"),
            "edition": copy.get("edition"),  # None: not recorded for this copy
            "copyright_date": copy.get("copyright_date"),
            "chapter_number": chapter.get("chapter_number") if chapter else None,
            "chapter_title": chapter.get("chapter_title") if chapter else None,
        },
        "page_as_cited": page,  # the citing book's number: a pointer, not a claim about this copy
        "page_reference": str(citation.get("to_page") or "")[:40] or None,  # as linked
        "match": "chapter" if chapter else "volume",
        # the chapter is found from the page in the DEFINING copy's pagination, not the cited
        # edition's (printings usually, not always, share pages)
        "match_basis": "page_in_defining_copy" if chapter else None,
        "candidate_chapters": (
            [c.get("chapter_number") for c in found] if len(found) > 1 else None
        ),
        "copy_recorded_at": copy.get(
            "at"
        ),  # when the defining copy's record was last written
        "supporting_copies": supporting,
        "linked_at": _now(),
    }


def write_links(
    store: Any,
    citations: List[Dict[str, Any]],
    volumes: Dict[str, Dict[str, Any]],
    books: Dict[str, Dict[str, Any]],
    partial: bool = False,
    out: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """Rebuild ``links/<citing book>.json`` for every citation of a held volume (a regenerable
    layer: rebuilt whole each run from the citation index; the original records are untouched).
    ``partial``: some citation records could not be read, so no link file is deleted this run.
    Concurrent Phase 3 tasks may both rewrite a link file: the last write wins and the next run
    converges (the inputs are per book)."""
    by_citing = _links_by_citing(citations, volumes, books)
    if out is not None:
        out.extend(link for links in by_citing.values() for link in links)
    counts = {"links": 0, "chapter": 0}
    for citing, links in by_citing.items():
        if _write(
            store,
            f"links/{_safe(citing)}.json",
            {"citing_book": citing, "links": links, "built_at": _now()},
        ):
            counts["links"] += len(links)
            counts["chapter"] += sum(1 for k in links if k["match"] == "chapter")
    if not partial:
        _drop_stale_links(store, {_safe(b) for b in by_citing})
    logger.info(
        "OBSERV corpus_links.links built=%d to_chapter=%d to_volume_only=%d books=%d",
        counts["links"],
        counts["chapter"],
        counts["links"] - counts["chapter"],
        len(by_citing),
    )
    return counts


def _links_by_citing(
    citations: List[Dict[str, Any]],
    volumes: Dict[str, Dict[str, Any]],
    books: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    by_citing: Dict[str, List[Dict[str, Any]]] = {}
    for rec in citations:
        v = volumes.get(rec.get("volume") or "") or {}
        copy = books.get(str(v.get("defining_copy")))
        if copy:
            citing = str(rec.get("cited_by_book") or "")
            by_citing.setdefault(citing, []).extend(_record_links(rec, citing, copy, v))
    return by_citing


def _record_links(
    rec: Dict[str, Any], citing: str, copy: Dict[str, Any], v: Dict[str, Any]
) -> List[Dict[str, Any]]:
    supporting = [b for b in v.get("held_as") or [] if b != copy.get("book")]
    return [link_for(c, citing, copy, supporting) for c in rec.get("citations") or []]


def _drop_stale_links(store: Any, keep: set) -> None:
    for path in _list(store, "links"):
        if path.rsplit("/", 1)[-1][: -len(".json")] not in keep:
            try:
                store.delete(path)
            except Exception as e:  # noqa: BLE001 - said; rebuilt next run
                _write_failed("delete", path, e)
