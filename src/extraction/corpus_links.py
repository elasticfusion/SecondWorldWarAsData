"""The citation index of ibiblio HyperWar volumes (owner 2026-11-24).

Every volume will be ingested in the end (owner), in batches the owner approves (Grok cost). Until a
cited volume is in the corpus, its citations wait here, so nothing is lost and the records to update
are known when it arrives.

Stored in the data bucket under ``corpus-links/`` (``output/_corpus_links/`` locally), outside
``output/`` so no sync or trigger touches it, and written per book so concurrent Phase 3 tasks never
write the same file:

* ``volumes/<book>.json`` - the volume a book IS (``{"book", "volume", "source_url"}``): the
  volume -> book table. Book slugs are immutable and need not equal the volume key (``XChannel`` and
  ``CrossChannelAttack`` are both ``USA-E-XChannel``), so volumes are matched by key, never by slug.
* ``citations/<volume>/<book>.json`` - every link from ``<book>`` to ``<volume>``: the citing
  SourceSectionID, chapter, URL and printed page (``to_page``). Rewritten whole on each run of the book
  (its citations are recomputed from its parsed documents), so it is idempotent.
* ``ingest-list.json`` - per cited volume: its index URL, how many links and books cite it, and
  whether it is held (``held_as``: the books that are it) or still ``pending``. The pending ones, most
  cited first, are the volumes to ingest; for a held one the citations are the records to update.

Connecting a citation to the held volume's records (a link on the citing source section, the
bibliography entry marked as in the corpus) is the next step (TODO, Group 9).
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


def record_book(
    book: str, source_url: str, chapters: Iterable[Dict[str, Any]]
) -> Dict[str, int]:
    """Record what ``book`` is and which other volumes it cites. ``chapters``: for each parsed
    document, ``{"chapter": ..., "edges": [...]}`` (``intra_corpus.process_doc_links`` edges).
    Returns {volumes_cited, citations}. A storage write error is said, not raised.
    """
    from src.extraction.intra_corpus import _volume_of

    store = _store()
    own = _volume_of(source_url)
    if not own:
        logger.warning(
            "OBSERV corpus_links.book_volume_unknown book=%r status=warn - the book's source URL "
            "is not an ibiblio HyperWar volume page, so it is not counted as holding any volume "
            "(its own volume may be listed as pending). Check the book's meta.yaml source_url",
            book[:80],
        )
    _write(
        store,
        f"volumes/{_safe(book)}.json",
        {"book": book, "volume": own, "source_url": source_url or None, "at": _now()},
    )
    by_volume = _citations_by_volume(chapters, own)
    if len(by_volume) > _MAX_VOLUMES_PER_BOOK:
        logger.warning(
            "OBSERV corpus_links.too_many_volumes book=%r volumes=%d cap=%d status=warn - only the "
            "most-cited are indexed; check the book's links (a real book cites a few volumes)",
            book[:80],
            len(by_volume),
            _MAX_VOLUMES_PER_BOOK,
        )
        top = sorted(by_volume, key=lambda v: -len(by_volume[v]))[
            :_MAX_VOLUMES_PER_BOOK
        ]
        by_volume = {v: by_volume[v] for v in top}
    total = 0
    for vol, cites in by_volume.items():
        total += min(len(cites), _MAX_CITATIONS)
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
                "volume_index_url": volume_index_url(
                    str(cites[0]["to_url"] or ""), vol
                ),
                "cited_by_book": book,
                "citations": cites[:_MAX_CITATIONS],
                "at": _now(),
            },
        )
    _drop_stale(store, book, set(by_volume))
    return {"volumes_cited": len(by_volume), "citations": total}


def _citations_by_volume(
    chapters: Iterable[Dict[str, Any]], own: Optional[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """The book's links to OTHER volumes, by volume, once per (chapter, URL)."""
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
            }
            by_volume.setdefault(vol, {})[(cite["chapter"], cite["to_url"])] = cite
    return {vol: list(cites.values()) for vol, cites in by_volume.items()}


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


def _read_all(store: Any, prefix: str) -> List[Dict[str, Any]]:
    out = []
    for path in _list(store, prefix):
        try:
            data = store.read_json(path)
        except Exception as e:  # noqa: BLE001 - said; that file is left out
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


def build_ingest_list(also_held: Iterable[str] = ()) -> Dict[str, Any]:
    """Rebuild ``ingest-list.json`` from every book's records. ``also_held``: volume keys known to
    be in the corpus from elsewhere (this task's source sections). Returns the list."""
    store = _store()
    held = _held(store, also_held)
    volumes = _cited(store)
    for vol, v in volumes.items():
        v["cited_by"] = sorted({b for b in v["cited_by"] if b})
        v["status"] = "held" if vol in held else "pending"
        v["held_as"] = sorted(set(held.get(vol, []))) or None
    ordered = sorted(
        volumes.values(), key=lambda v: (v["status"] != "pending", -v["links"])
    )
    pending = sum(1 for v in ordered if v["status"] == "pending")
    result = {
        "built_at": _now(),
        "pending": pending,
        "held": len(ordered) - pending,
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


def _held(store: Any, also_held: Iterable[str]) -> Dict[str, List[str]]:
    """Volume key -> the books that are it."""
    held: Dict[str, List[str]] = {v: [] for v in also_held if v}
    for rec in _read_all(store, "volumes"):
        if rec.get("volume"):
            held.setdefault(rec["volume"], []).append(str(rec.get("book")))
    return held


def _cited(store: Any) -> Dict[str, Dict[str, Any]]:
    """Volume key -> its index URL, link count and citing books, from every book's citations."""
    volumes: Dict[str, Dict[str, Any]] = {}
    for rec in _read_all(store, "citations"):
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
