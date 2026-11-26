"""Mark a bibliography record whose cited work we hold in the corpus (owner 2026-11-24).

A HyperWar footnote that links another volume ("Taylor, _[Omaha Beachhead](../USA-A-Omaha/...)_,
p. 31") became a bibliography record in Phase 2 (``bibliography.extract_bibliography_deterministic``:
one mention per note, named by the citing book, the parsed chapter file and the note number). The
citation index (``corpus_links``) links the same note to the volume's defining copy. When that copy
is held, the record says so:

``held_in_corpus``: ``BookID``, ``book``, ``volume``, ``format``, ``edition``, ``copyright_date`` of the
defining copy (owner rule: the PDF defines a book), ``linked_at``, and ``evidence`` - the notes
(book, chapter, note number, URL) whose links made the match.

The match is exact: (citing book folder, parsed chapter, note number) against the record's mentions.
A note citing several works was split by Phase 2 into records ``3``, ``3b`` ...; Phase 3 splits it the
same way, so a link carries the number of the part it is in. A record whose notes link two volumes is
not guessed (counted and said). Only links from a note can match (a link in the narrative has no
bibliography record). A mark is rewritten only when it changes, and cleared when the record no
longer matches a held volume (only after a run that read every citation record). Nothing else on the
record changes.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_EVIDENCE = 20
_Key = Tuple[str, str, str]  # (book, chapter, reference_number)


def _key(book: Any, chapter: Any, number: Any) -> Optional[_Key]:
    if not (book and chapter and number not in (None, "")):
        return None
    return (str(book), str(chapter), str(number))


def _record(f: Path) -> Optional[Dict[str, Any]]:
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # unreadable records are reported by the bibliography steps
    return rec if isinstance(rec, dict) else None


def _index(bib_dir: Path) -> Tuple[Dict[_Key, List[Path]], List[Path]]:
    """(citing book, parsed chapter, note number) -> the bibliography records with that mention;
    and the records that carry a mark now."""
    from src.utils.entity_records import entity_files

    index: Dict[_Key, List[Path]] = {}
    marked: List[Path] = []
    for f in entity_files(bib_dir.parent, f"output/{bib_dir.name}/*.json"):
        rec = _record(f)
        if rec is None:
            continue
        if rec.get("held_in_corpus"):
            marked.append(f)
        for m in rec.get("mentions") or []:
            if not isinstance(m, dict):
                continue
            k = _key(m.get("book"), m.get("chapter"), m.get("reference_number"))
            if k and f not in index.setdefault(k, []):
                index[k].append(f)
    return index, marked


def _held_entry(link: Dict[str, Any]) -> Dict[str, Any]:
    cited = link.get("cited") or {}
    return {
        k: (str(cited[k])[:300] if cited.get(k) is not None else None)
        for k in ("BookID", "book", "volume", "format", "edition", "copyright_date")
    }


def _evidence(link: Dict[str, Any]) -> Dict[str, Any]:
    citing = link.get("citing") or {}
    url = str(citing.get("url") or "")
    return {
        "book": citing.get("parsed_book") or citing.get("book"),
        "chapter": citing.get("parsed_chapter"),
        "reference_number": citing.get("reference_number"),
        # only a web link is kept, at most 2048 characters (published with the record)
        "url": url[:2048] if url.lower().startswith(("http://", "https://")) else None,
    }


def match_links(
    links: Iterable[Dict[str, Any]], index: Dict[_Key, List[Path]]
) -> Tuple[Dict[Path, Dict[str, Any]], Dict[str, int]]:
    """record file -> its ``held_in_corpus`` (without ``linked_at``), and counts of what could not
    be matched (narrative, no_record, ambiguous = one note in several records, conflicting = one
    record's notes linking two volumes)."""
    marks: Dict[Path, Dict[str, Any]] = {}
    conflicting: set = set()
    counts = {"narrative": 0, "no_record": 0, "ambiguous": 0, "conflicting": 0}
    for link in links:
        f = _the_record(link, index, counts)
        if f is None:
            continue
        held = _held_entry(link)
        mark = marks.setdefault(f, {**held, "evidence": []})
        if mark["volume"] != held["volume"]:
            conflicting.add(f)  # one record's notes point at two volumes: not guessed
            continue
        ev = _evidence(link)
        if len(mark["evidence"]) < _MAX_EVIDENCE and ev not in mark["evidence"]:
            mark["evidence"].append(ev)
    for f in conflicting:
        marks.pop(f, None)
    counts["conflicting"] = len(conflicting)
    return marks, counts


def _the_record(
    link: Dict[str, Any], index: Dict[_Key, List[Path]], counts: Dict[str, int]
) -> Optional[Path]:
    """The one record a link's note made, or None (counted: narrative, no_record, ambiguous)."""
    citing = link.get("citing") or {}
    k = _key(
        citing.get("parsed_book") or citing.get("book"),
        citing.get("parsed_chapter"),
        citing.get("reference_number"),
    )
    if k is None:
        counts["narrative"] += 1
        return None
    found = index.get(k) or []
    if len(found) != 1:
        counts["no_record" if not found else "ambiguous"] += 1
        return None
    return found[0]


def mark_held(
    output_root: Path, links: List[Dict[str, Any]], clear_stale: bool = True
) -> int:
    """Set ``held_in_corpus`` on every bibliography record a held volume's link matches, and
    (``clear_stale``) clear it on records it no longer matches. Returns the records written.
    Fail-safe per record."""
    bib_dir = Path(output_root) / "bibliography"
    noted = [k for k in links if (k.get("citing") or {}).get("reference_number")]
    if not noted and not clear_stale:
        logger.info(
            "OBSERV bibliography_corpus.marked matched=0 written=0 (no note links)"
        )
        return 0
    index, marked = _index(bib_dir)
    marks, counts = match_links(noted, index)
    counts["narrative"] = len(links) - len(noted)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    result = {"written": 0, "cleared": 0, "blocked": 0}
    for f, mark in marks.items():
        _update(f, mark, now, result)
    if clear_stale:
        for f in marked:
            if f not in marks:
                _update(f, None, now, result)
    _say(len(marks), result, counts)
    return result["written"] + result["cleared"]


def _update(
    f: Path, mark: Optional[Dict[str, Any]], now: str, result: Dict[str, int]
) -> None:
    """Write ``mark`` (None: clear it) onto the record at ``f`` when it changes. Never raises."""
    from src.utils.file_lock import write_json_with_lock

    rec = _record(f)
    if rec is None:
        return
    old = dict(rec.get("held_in_corpus") or {})
    old.pop("linked_at", None)
    if (mark or {}) == old:
        return  # unchanged: not rewritten
    if mark is None:
        rec.pop("held_in_corpus", None)
    else:
        rec["held_in_corpus"] = {**mark, "linked_at": now}
    try:
        ok = write_json_with_lock(f, rec, entity="bibliography")
    except Exception as e:  # noqa: BLE001 - one record must not stop the rest
        logger.warning(
            "OBSERV bibliography_corpus.write_error record=%s err=%s status=warn - the mark is "
            "retried next run. Check disk space and the record",
            f.name,
            type(e).__name__,
        )
        ok = False
    if not ok:
        result["blocked"] += 1
    elif mark is None:
        result["cleared"] += 1
    else:
        result["written"] += 1


def _say(matched: int, result: Dict[str, int], counts: Dict[str, int]) -> None:
    logger.info(
        "OBSERV bibliography_corpus.marked matched=%d written=%d cleared=%d narrative=%d "
        "no_record=%d ambiguous=%d conflicting=%d blocked=%d",
        matched,
        result["written"],
        result["cleared"],
        counts["narrative"],
        counts["no_record"],
        counts["ambiguous"],
        counts["conflicting"],
        result["blocked"],
    )
    if result["blocked"] or counts["ambiguous"] or counts["conflicting"]:
        logger.warning(
            "OBSERV bibliography_corpus.unmarked ambiguous=%d conflicting=%d blocked=%d "
            "status=warn - these cited works are held but their bibliography record was not "
            "marked (or a stale mark not cleared): ambiguous = one note in several records, "
            "conflicting = one record whose notes link two volumes (check the record's "
            "mentions), blocked = the write failed or the schema guard refused it (see its line)",
            counts["ambiguous"],
            counts["conflicting"],
            result["blocked"],
        )
