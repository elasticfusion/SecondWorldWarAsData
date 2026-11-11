#!/usr/bin/env python3
"""Provenance validation for ONE book: is every record's cited source content TRUE?

The completeness audits (``src/utils/provenance_integrity``: a record HAS a source edge;
``src/utils/referential_integrity``: every id resolves) cannot see a record whose links all
resolve but point at the wrong place, or whose "verbatim" quote is not in the book. This checks the
content behind the links, against the book's own parsed text.

Phase 2 (extraction - run at the end of Phase 2, and again at the end of Phase 3, which merges and
dedups records and can break a link):

* ``P2-L1`` sub-event link: a mention's Sub-eventID belongs to the Event it names (hard);
* ``P2-L2`` paragraph locator: a cited paragraph exists in the chapter and lies in the cited
  sub-event (hard);
* ``P2-Q1`` quote: ``original_text`` is in the cited paragraphs / sub-event (whole words, exact
  after normalising; an elided "A ... B" is its parts; a quote may run across two consecutive
  paragraphs). Found elsewhere in the chapter = the wrong location (hard); not in the book (hard);
  a near match only, or a quote under ``MIN_VERIFY_WORDS`` not found in place = warning. A
  record-level quote (dates, logistics, casualties) only warns;
* ``P2-Q2`` note quote: a bibliography mention's ``verbatim_reference`` is the text of the note it
  cites (hard when absent from the chapter's notes; warning when the note text itself is empty, or
  it sits under another note number).

Phase 3 (enrichment - run at the end of Phase 3, after every merge: the dedup auto-merge, the
bibliography reconcile and Dedup Review UI merges all run AFTER the Phase-2 gate): the Phase-2
checks again, plus

* ``P3-B1`` real source: a bibliography entry of the book has a real title - a stub (the
  extractor's own test: ``ibid``, ``op. cit.``, a bare short number, empty; or ``Unknown``) fails; one Phase 3 classed ``not_citation`` warns (awaits disposition);
* ``P3-R1`` rights: an empty ``license`` fails; ``unknown`` license, no ``copyright_status``, or a
  NARA-searchable source (the resolver's own predicate) with no ``archive_reference_number`` warn;
* ``P3-A1`` attribution: an enriched people / people_groups / equipment record of the book names
  its source (``entity_records.has_source_link``, the P3-A5 predicate) - hard;
* ``P3-C1`` citations resolve: every note a sub-event cites (``Footnote_References`` /
  ``Endnote_References``) that names a source (not a stub, which the extractor skips) has a
  bibliography mention - one warning per chapter, with counts.

A mention is in this book when its EventID is one of the book's events
(``output/content/<book>/*-event.json``); records are shared across books, so the check is per
mention. An id that resolves nowhere is referential_integrity's finding, not repeated here.

Usage:
  python -m scripts.validate_provenance --book USA-E-XChannel [--output-root output] [--phase 2]
Exit: 0 = PASS (no hard failure); 1 = FAIL. The full findings go to
``<output-root>/reports/provenance/<book>-phase<N>.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from urllib.parse import urlparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from src.schemas import entity_registry as _er
from src.utils.entity_records import (
    ENRICHED_ENTITIES,
    entity_files,
    has_source_link,
    is_enriched,
    iter_mentions,
    load_json,
    mention_sub_event,
)
from src.utils.paragraph_numbers import coerce_paragraph_numbers
from src.utils.quote_match import (
    EXACT,
    NEAR,
    Index,
    build_index,
    contains,
    in_paragraphs,
    normalize,
    too_short,
)

logger = logging.getLogger(__name__)

HARD, WARN = "fail", "warn"
# Entities whose records cite the book's text (images/maps/source_section cite no book quote).
_NOT_TEXT_CITING = {"events", "images", "maps", "map_features", "source_section"}
_SAMPLES_PER_CHECK = 5


# --- the book's source ---------------------------------------------------------------------------
@dataclass
class Chapter:
    stem: str  # e.g. "chapter8" (the -parsed/-event/-endnotes files share it)
    index: Index
    numbers: FrozenSet[int]
    # note number -> its note texts as in the source (footnotes AND endnotes may share a number)
    notes: Dict[str, List[str]]
    owner: Dict[int, str] = field(default_factory=dict)  # paragraph -> its sub-event
    notes_event: bool = False  # a -notes-event: its "paragraphs" are note texts


@dataclass
class Book:
    slug: str
    chapters: Dict[str, Chapter] = field(default_factory=dict)  # EventID -> chapter
    sub_events: Dict[str, Tuple[str, FrozenSet[int]]] = field(default_factory=dict)
    degraded: set = field(default_factory=set)  # Sub-eventIDs with no source paragraphs
    # Sub-eventID -> the note numbers it cites (Footnote_References / Endnote_References)
    cited_notes: Dict[str, set] = field(default_factory=dict)


def _notes(
    parsed: Dict[str, Any], endnotes: Optional[Dict[str, Any]]
) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for n in list(parsed.get("footnotes") or []) + list(
        (endnotes or {}).get("endnotes") or []
    ):
        if isinstance(n, dict) and n.get("number") is not None:
            out.setdefault(str(n["number"]).strip(), []).append(
                str(n.get("text") or "")
            )
    return out


def _sub_event_paragraphs(se: Dict[str, Any]) -> FrozenSet[int]:
    """The paragraphs a sub-event holds: its ``Sub-event_fulltext`` ``Paragraph_<n>`` keys."""
    nums = coerce_paragraph_numbers(
        [k for k in (se.get("Sub-event_fulltext") or {}) if k.startswith("Paragraph_")],
        scope="provenance.sub_event",
        subject=str(se.get("Sub-eventID", "")),
    )
    return frozenset(nums)


def _cited_notes(ev: Dict[str, Any]) -> Dict[str, set]:
    """Sub-eventID -> the note numbers its ``Footnote_References`` / ``Endnote_References`` cite."""
    out: Dict[str, set] = {}
    for se in ev.get("Sub-events") or []:
        if not isinstance(se, dict) or not se.get("Sub-eventID"):
            continue
        refs = list(se.get("Footnote_References") or []) + list(
            se.get("Endnote_References") or []
        )
        nums = {str(r).strip() for r in refs if not isinstance(r, (dict, list))}
        if nums:
            out[str(se["Sub-eventID"])] = nums
    return out


def _note_sub_events(
    ev: Dict[str, Any], start: int
) -> Tuple[Index, Dict[str, FrozenSet[int]]]:
    """A NOTES event (``chapterN-notes-event.json``, built from the chapter's notes) holds each note
    as a sub-event whose fulltext is the note text: index those texts as pseudo-paragraphs
    (numbered below ``start``, never a real paragraph number) so its mentions are checked like any
    other."""
    index: Index = []
    owned: Dict[str, FrozenSet[int]] = {}
    n = start
    for se in ev.get("Sub-events") or []:
        if not isinstance(se, dict) or not se.get("Sub-eventID"):
            continue
        texts = (se.get("Sub-event_fulltext") or {}).values()
        nums = []
        for text in texts:
            n -= 1
            index.append((n, normalize(text)))
            nums.append(n)
        owned[str(se["Sub-eventID"])] = frozenset(nums)
    return index, owned


def _add_chapter(book: Book, folder: Path, ev_file: Path) -> None:
    stem = ev_file.name[: -len("-event.json")]
    notes_event = stem.endswith("-notes")
    parsed_stem = stem[: -len("-notes")] if notes_event else stem
    event, parsed = load_json(ev_file), load_json(folder / f"{parsed_stem}-parsed.json")
    ev = event.get("Event") if isinstance(event, dict) else None
    if not isinstance(ev, dict) or not isinstance(parsed, dict):
        logger.warning(
            "OBSERV provenance.chapter_unreadable book=%s file=%s", book.slug, stem
        )
        return
    event_id = str(ev.get("EventID"))
    if event_id in book.chapters:
        logger.warning(
            "OBSERV provenance.duplicate_event_id status=warn book=%s file=%s EventID=%s "
            "(two event files share an EventID; the later one is not validated)",
            book.slug,
            stem,
            event_id,
        )
        return
    index = build_index(parsed.get("paragraphs") or [])
    if notes_event:
        extra, owned = _note_sub_events(ev, start=0)
        index += extra
    else:
        owned = {
            str(se["Sub-eventID"]): _sub_event_paragraphs(se)
            for se in ev.get("Sub-events") or []
            if isinstance(se, dict) and se.get("Sub-eventID")
        }
        book.cited_notes.update(_cited_notes(ev))
    endnotes = load_json(folder / f"{parsed_stem}-endnotes.json")
    book.chapters[event_id] = Chapter(
        stem,
        index,
        frozenset(n for n, _ in index),
        _notes(parsed, endnotes if isinstance(endnotes, dict) else None),
        notes_event=notes_event,
    )
    chapter = book.chapters[event_id]
    for seid, paras in owned.items():
        book.sub_events[seid] = (event_id, paras)
        chapter.owner.update({n: seid for n in paras})
        if not paras:
            book.degraded.add(seid)


def load_book(root: Path, slug: str) -> Book:
    """The book's events, sub-events and chapter text from ``content/<slug>/``."""
    book = Book(slug)
    folder = root / "content" / slug
    for ev_file in sorted(folder.glob("*-event.json")):
        _add_chapter(book, folder, ev_file)
    return book


# --- findings ------------------------------------------------------------------------------------
@dataclass
class Findings:
    book: str
    phase: str
    checked: Counter = field(default_factory=Counter)
    failed: Counter = field(default_factory=Counter)
    warned: Counter = field(default_factory=Counter)
    items: List[Dict[str, Any]] = field(default_factory=list)

    def add(
        self, level: str, check: str, where: str, reason: str, **detail: Any
    ) -> None:
        (self.failed if level == HARD else self.warned)[check] += 1
        self.items.append(
            {"level": level, "check": check, "where": where, "reason": reason, **detail}
        )

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_json(self) -> Dict[str, Any]:
        return {
            "book": self.book,
            "phase": self.phase,
            "result": "PASS" if self.ok else "FAIL",
            "checked": dict(self.checked),
            "failed": dict(self.failed),
            "warned": dict(self.warned),
            "findings": self.items,
        }


def _snippet(text: Any) -> str:
    return " ".join(str(text or "").split())[:120]


# --- Phase-2 checks ------------------------------------------------------------------------------
def _scope(
    book: Book, event_id: str, seid: str
) -> Tuple[Optional[FrozenSet[int]], bool]:
    """(the paragraphs a mention may cite - its sub-event's, else None = the whole chapter,
    whether its sub-event belongs to its event)."""
    if not seid or seid not in book.sub_events:
        return None, True  # unanchored, or dangling (referential_integrity's finding)
    owner, paras = book.sub_events[seid]
    return paras, owner == event_id


def check_link(
    f: Findings, where: str, book: Book, m: Dict[str, Any]
) -> Optional[FrozenSet[int]]:
    """P2-L1. Returns the mention's paragraph scope (None = the whole chapter)."""
    event_id, seid = str(m.get("EventID")), mention_sub_event(m)
    scope, belongs = _scope(book, event_id, seid)
    if seid and seid in book.sub_events:
        f.checked["P2-L1"] += 1
        if not belongs:
            f.add(
                HARD,
                "P2-L1",
                where,
                "sub-event belongs to another event",
                EventID=event_id,
                SubEventID=seid,
            )
            return None
    return scope


def check_locator(
    f: Findings,
    where: str,
    chapter: Chapter,
    scope: Optional[FrozenSet[int]],
    m: Dict[str, Any],
) -> List[int]:
    """P2-L2. Returns the mention's readable cited paragraph numbers."""
    nums = coerce_paragraph_numbers(
        m.get("paragraph_numbers"), scope="provenance.locator", subject=where
    )
    for n in nums:
        f.checked["P2-L2"] += 1
        if n not in chapter.numbers:
            f.add(
                HARD,
                "P2-L2",
                where,
                "cited paragraph is not in the chapter",
                paragraph=n,
            )
        elif scope is not None and not chapter.notes_event and n not in scope:
            f.add(
                HARD,
                "P2-L2",
                where,
                "cited paragraph is outside the cited sub-event",
                paragraph=n,
            )
    return nums


def check_quote(
    f: Findings,
    where: str,
    chapter: Chapter,
    cited: Optional[Iterable[int]],
    quote: Any,
    level: str = HARD,
) -> None:
    """P2-Q1: ``quote`` is in the ``cited`` paragraphs (None = anywhere in the chapter).

    Not there: in another part of the chapter = the wrong location; nowhere = not in the book
    (both at ``level``, hard by default); a near match only, or a quote too short to prove a
    location by = a warning."""
    if not str(quote or "").strip():
        return  # an empty quote: validate_phase2's A3 warning, not repeated here
    f.checked["P2-Q1"] += 1
    num, how = in_paragraphs(quote, chapter.index, cited)
    if how == EXACT:
        return
    elsewhere, how_else = (
        in_paragraphs(quote, chapter.index) if cited is not None else (None, "")
    )
    short = too_short(quote)
    if how == NEAR or (short and how_else != EXACT):
        reason = "near match only" if how == NEAR else "quote too short to verify"
        f.add(WARN, "P2-Q1", where, reason, found_in=num, quote=_snippet(quote))
    elif how_else == EXACT:
        f.add(
            WARN if short else level,
            "P2-Q1",
            where,
            "quote is in another part of the chapter",
            found_in=elsewhere,
            found_in_sub_event=chapter.owner.get(elsewhere or 0, ""),
            quote=_snippet(quote),
        )
    else:
        f.add(level, "P2-Q1", where, "quote is not in the book", quote=_snippet(quote))


def _quotes_note(quote: str, note: str) -> bool:
    """The quote is (part of) the note, or the whole note is in the quote - a note of fewer than
    ``MIN_VERIFY_WORDS`` ("Ibid.") is never proof of the latter."""
    return contains(note, quote) or (not too_short(note) and contains(quote, note))


def check_note(f: Findings, where: str, chapter: Chapter, m: Dict[str, Any]) -> None:
    """P2-Q2: a bibliography mention quotes the note it cites."""
    quote = normalize(m.get("verbatim_reference"))
    number = str(m.get("reference_number") or "").strip()
    f.checked["P2-Q2"] += 1
    if not quote:
        if not number:  # only its sub-event says where the source is cited
            f.add(
                WARN, "P2-Q2", where, "no note locator (no reference_number or quote)"
            )
        return
    texts = [normalize(t) for t in chapter.notes.get(number, [])]
    if texts and not any(texts):
        f.add(
            WARN,
            "P2-Q2",
            where,
            "the cited note has no text in the source",
            note=number,
        )
        return
    if any(_quotes_note(quote, t) for t in texts):
        return
    if any(
        _quotes_note(quote, normalize(t)) for ts in chapter.notes.values() for t in ts
    ):
        f.add(WARN, "P2-Q2", where, "quote is under another note number", note=number)
        return
    f.add(
        HARD,
        "P2-Q2",
        where,
        "quote is not in the chapter's notes",
        note=number,
        quote=_snippet(m.get("verbatim_reference")),
    )


def _check_mention(f: Findings, book: Book, where: str, m: Dict[str, Any]) -> None:
    chapter = book.chapters[str(m.get("EventID"))]
    scope = check_link(f, where, book, m)
    cited = check_locator(f, where, chapter, scope, m)
    if "verbatim_reference" in m:
        check_note(f, where, chapter, m)
        return
    # a cited paragraph that is not in the chapter is P2-L2's finding: quote-check the rest
    real = [n for n in cited if n in chapter.numbers]
    if not real and mention_sub_event(m) in book.degraded:
        # the sub-event lost its paragraphs: the quote can still be proven in the chapter
        scope = None
    check_quote(f, where, chapter, real or scope, m.get("original_text"))


def _check_record(
    f: Findings, book: Book, entity: str, name: str, rec: Dict[str, Any]
) -> None:
    # casualties: their one event_context (no original_text: the record-level quote below)
    mentions = [m for m in iter_mentions(rec) if isinstance(m, dict)]
    ours = [m for m in mentions if str(m.get("EventID")) in book.chapters]
    for i, m in enumerate(ours):
        _check_mention(f, book, f"{entity}/{name}#{m.get('MentionID') or i}", m)
    # a record-level quote (dates, logistics, casualties) is checked only when every mention of
    # the record is in ONE chapter of this book (else it may quote another one), and only warns:
    # it is often the extractor's summary, not a quote
    chapters = {str(m.get("EventID")) for m in ours}
    if (
        ours
        and len(ours) == len(mentions)
        and len(chapters) == 1
        and isinstance(rec.get("original_text"), str)
    ):
        check_quote(
            f,
            f"{entity}/{name}",
            book.chapters[chapters.pop()],
            None,
            rec["original_text"],
            level=WARN,
        )


# --- Phase-3 checks ------------------------------------------------------------------------------
def _is_stub(text: str) -> bool:
    """A note / title that names no source: the extractor's own test (``ibid.``, ``op. cit.``,
    ``see n. 5``, under four characters), plus the placeholder title ``Unknown``."""
    from src.extraction.bibliography import _is_stub_citation

    return _is_stub_citation(text) or text.strip().lower() == "unknown"


def is_nara_source(rec: Dict[str, Any]) -> bool:
    """Phase 3 found the source at NARA: resolved through it (``search_source`` ``nara_catalog`` /
    ``nara_grok``) or holding a catalog.archives.gov copy. Not the resolver's routing test
    (``_is_nara_searchable``), which sends every citation-shaped entry to NARA to look.
    """
    if str(rec.get("search_source") or "").lower().startswith("nara"):
        return True
    urls = re.findall(r"https?://[^\s\"']+", json.dumps(rec.get("resource_urls") or []))
    return any(_is_nara_host(urlparse(u).hostname or "") for u in urls)


def _is_nara_host(host: str) -> bool:
    return host == "archives.gov" or host.endswith(".archives.gov")


def check_bib_source(f: Findings, where: str, rec: Dict[str, Any]) -> None:
    """P3-B1 (a real source) + P3-R1 (its rights fields) for a bibliography entry of the book."""
    f.checked["P3-B1"] += 1
    title = str(rec.get("title") or "")
    if _is_stub(title):
        f.add(HARD, "P3-B1", where, "stub entry, not a source", title=title[:40])
    elif rec.get("search_status") == "not_citation":
        f.add(WARN, "P3-B1", where, "classed not_citation (awaits disposition)")
    f.checked["P3-R1"] += 1
    license_ = str(rec.get("license") or "").strip()
    if not license_:
        f.add(HARD, "P3-R1", where, "no license")
    elif license_ == "unknown":
        f.add(WARN, "P3-R1", where, "license unknown")
    if not rec.get("copyright_status"):
        f.add(WARN, "P3-R1", where, "no copyright_status")
    ref = str(rec.get("archive_reference_number") or "").strip()
    if (not ref or ref == "None") and is_nara_source(rec):
        f.add(WARN, "P3-R1", where, "NARA source without an archive_reference_number")


def check_attribution(f: Findings, where: str, rec: Dict[str, Any]) -> None:
    """P3-A1: an enriched record names where its enrichment came from."""
    if not is_enriched(rec):
        return
    f.checked["P3-A1"] += 1
    if not has_source_link(rec):
        f.add(HARD, "P3-A1", where, "enriched but names no source (un-attributable)")


def check_citations_resolve(f: Findings, book: Book, bib_mentions: set) -> None:
    """P3-C1: every note a sub-event cites that names a source (not a stub - those the extractor
    skips by design) has a bibliography mention (by sub-event + note number). One finding per
    chapter, with counts (one per note would bury the signal)."""
    cited: Counter = Counter()
    missing: Counter = Counter()
    for seid, nums in book.cited_notes.items():
        event_id = book.sub_events.get(seid, ("", frozenset()))[0]
        chapter = book.chapters.get(event_id)
        for n in nums:
            texts = chapter.notes.get(n, []) if chapter else []
            if texts and all(_is_stub(t) for t in texts):
                continue  # a stub note ("Ibid."): no source of its own to resolve
            f.checked["P3-C1"] += 1
            cited[event_id] += 1
            if (seid, n) not in bib_mentions:
                missing[event_id] += 1
    for event_id, n_missing in missing.items():
        chapter = book.chapters.get(event_id)
        f.add(
            WARN,
            "P3-C1",
            f"content/{book.slug}/{chapter.stem if chapter else event_id}",
            "cited notes with no bibliography entry",
            missing=n_missing,
            cited=cited[event_id],
        )


def _phase3_record(
    f: Findings, book: Book, entity: str, name: str, rec: Dict[str, Any], bib: set
) -> None:
    """The Phase-3 checks of one record of the book (any mention in the book)."""
    ours = [
        m
        for m in iter_mentions(rec)
        if isinstance(m, dict) and str(m.get("EventID")) in book.chapters
    ]
    if not ours:
        return
    if entity == "bibliography":
        check_bib_source(f, f"{entity}/{name}", rec)
        bib.update(
            (mention_sub_event(m), str(m.get("reference_number") or "").strip())
            for m in ours
        )
    elif entity in ENRICHED_ENTITIES:
        check_attribution(f, f"{entity}/{name}", rec)


PHASE_CHECKS = {"2": "P2", "3": "P3"}
# the entities whose records cite the book's text (the corpus a validation run needs)
_TEXT_CITING_SPECS = tuple(
    spec
    for spec in _er.ENTITY_REGISTRY
    if spec.name not in _NOT_TEXT_CITING and spec.output_glob
)
TEXT_CITING_ENTITIES = tuple(spec.name for spec in _TEXT_CITING_SPECS)
GATE_MODES = ("warn", "block")


def gate_mode() -> str:
    """What a failed validation does at a phase end: ``warn`` (report + alert; the book goes on)
    or ``block`` (the next phase is held for review). ``PROVENANCE_GATE`` env (a no-rebuild
    switch) over ``provenance_validation.gate`` in config.yaml; ``warn`` when unset/unreadable.
    """
    mode = os.environ.get("PROVENANCE_GATE", "").strip().lower()
    if not mode:
        try:
            from src.utils.config import load_config

            mode = str(
                (load_config().get("provenance_validation") or {}).get("gate", "")
            ).lower()
        except Exception:  # noqa: BLE001 - a config read error is not a mode
            mode = ""
    return mode if mode in GATE_MODES else "warn"


def validate(root: Path, slug: str, phase: str = "2") -> Findings:
    """Every check for ``phase`` over one book's records. Never raises on a bad record."""
    f = Findings(slug, phase)
    book = load_book(Path(root), slug)
    if not book.chapters:
        f.add(
            HARD,
            "P2-SRC",
            f"content/{slug}",
            "no readable event + parsed chapter files",
        )
        return f
    bib_mentions: set = (
        set()
    )  # (Sub-eventID, note number) the book's bibliography holds
    for spec in _TEXT_CITING_SPECS:
        for path in entity_files(Path(root), spec.output_glob):
            rec = load_json(path)
            if not isinstance(rec, dict):
                continue
            _check_record(f, book, spec.name, path.name, rec)  # Phase 2: in every phase
            if phase == "3":
                _phase3_record(f, book, spec.name, path.name, rec, bib_mentions)
    if phase == "3":
        check_citations_resolve(f, book, bib_mentions)
    return f


def write_report(root: Path, f: Findings) -> Path:
    out = Path(root) / report_key(f.book, f.phase)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(f.to_json(), indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return out


def report_key(book: str, phase: str) -> str:
    """The report's path under the output root (also its S3 key under ``output/``)."""
    return f"reports/provenance/{book}-phase{phase}.json"


def summary_lines(f: Findings) -> List[str]:
    """Counts per check + a few samples: the log and alert text (ids, reasons; no record text)."""
    lines = [
        f"Provenance validation: book={f.book} phase={f.phase} result={'PASS' if f.ok else 'FAIL'}"
    ]
    for check in sorted(f.checked):
        lines.append(
            f"  {check}: checked={f.checked[check]} failed={f.failed[check]} "
            f"warned={f.warned[check]}"
        )
    for check in sorted(f.failed):
        samples = [i for i in f.items if i["check"] == check and i["level"] == HARD]
        for s in samples[:_SAMPLES_PER_CHECK]:
            lines.append(f"    FAIL {check} {s['where']}: {s['reason']}")
    return lines


def validate_and_record(
    root: Path, book: str, phase: str = "2"
) -> Tuple[Findings, Path]:
    """Validate, write the report, log the summary and emit ``OBSERV provenance.validation``
    (status=error on a hard failure: escalated to the operator, with the report's location).
    """
    from src.utils.observe import observe

    f = validate(Path(root), book, phase)
    report = write_report(Path(root), f)
    for line in summary_lines(f):
        logger.info(line)
    observe(
        "provenance",
        "validation",
        status="ok" if f.ok else "error",
        note=(
            ""
            if f.ok
            else "records whose cited source content is wrong - see the report"
        ),
        book=book,
        phase=phase,
        failed=sum(f.failed.values()),
        warned=sum(f.warned.values()),
        report=f"output/{report_key(book, phase)}",
    )
    return f, report


def run(output_root: str, book: str, phase: str = "2") -> int:
    f, _ = validate_and_record(Path(output_root), book, phase)
    for line in summary_lines(f):
        print(line)
    return 0 if f.ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Provenance validation for one book")
    p.add_argument(
        "--book", required=True, help="the book's slug (output/content/<slug>/)"
    )
    p.add_argument("--output-root", default="output")
    p.add_argument("--phase", default="2", choices=sorted(PHASE_CHECKS))
    args = p.parse_args()
    return run(args.output_root, args.book, args.phase)


if __name__ == "__main__":
    sys.exit(main())
