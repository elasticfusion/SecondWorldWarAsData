"""Normalise one chapter of Chandra OCR markdown into the form Phase 1 parses.

Chandra's markdown of a scanned book is not what the parser reads (measured on Cross-Channel
Attack chapter VIII, PDF pages 288-354: of 504 parsed "paragraphs", 102 were footnotes read as
narrative, 89 kept raw ``<sup>68</sup>`` markers, 26 were image paragraphs, 0 footnotes, 0 page
markers). Like src/ingestion/hyperwar_html does for HyperWar's HTML, this rewrites it:

* **notes** - each footnote is its own paragraph opening ``<sup>N</sup>``, printed at the foot of
  its page (Chandra may set a page's notes between two halves of a body paragraph). They are
  gathered into ONE ``### Footnotes`` section in the definition form src/notes parses, and the
  body's ``<sup>N</sup>`` markers become the anchor form it canonicalises. A number defined twice
  keeps its first definition (src/notes reports the repeat);
* **paragraphs across breaks** - a body paragraph cut by a page break, a page's notes, a photo
  page or a caption continues in the next paragraph that opens with a lower-case letter; it is
  rejoined (with the page anchor inline, so its pages are kept). A word hyphenated across the break
  ("mar-" / "shaling") loses the hyphen unless the book itself prints it hyphenated ("counter-" /
  "attack" stays "counter-attack" only if the book prints that more often). A lower-case
  paragraph with no open body paragraph to continue, but an open note before it, continues that
  note (a note run over onto the next page);
* **images** - a paragraph opening ``![...](...)`` is the picture plus Chandra's MODEL-WRITTEN
  description of it, not the book's text: it is removed from the narrative and listed (file,
  PDF page) so the figure can be linked to the PDF's own image (src/ingestion/pdf_figures). The
  book's printed caption (its own paragraph) stays. A map plate's legend, scale and place
  labels are not narrative either: on a page with a map and no prose they are removed, the
  map's printed title kept with its figure;
* **pages** - the ``<a id="pageN"></a>`` anchors (N = PDF page) stay, for the parser's page
  numbers; citation itself is by chapter and paragraph number (owner 2026-11-08).

Every judgement is counted in the log. Deterministic, no model calls, no AWS.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from src.ingestion.pdf_html_crosscheck import is_chandra_caption

_ANCHOR = re.compile(r'<a id="page(\d+)"></a>')
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_NOTE_DEF = re.compile(r"^<sup>\s{0,2}(\d{1,4})\s{0,2}</sup>\s*(.*)$", re.S)
_NOTE_MARK = re.compile(r"<sup>\s{0,2}(\d{1,4})\s{0,2}</sup>")
_IMAGE = re.compile(r"^!\[([^\]]{0,2000})\]\(([^)\s]{1,500})\)")
_HEADING = re.compile(r"^#{1,6}\s")
_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
# A paragraph ending in one of these is complete; anything else may continue after a break.
_TERMINAL = tuple(".:;!?\"”’)']")
_WORD_END = re.compile(r"([^\W\d_]+)-$")
_WORD_START = re.compile(r"^([^\W\d_]+)")


@dataclass
class Figure:
    page: Optional[int]
    file: str
    model_description: (
        str  # Chandra's alt text: model-written, never the book's caption
    )
    printed_title: str = (
        ""  # a map plate's own printed title ("SITUATION IN EUROPE 6 June 1944")
    )


@dataclass
class NormaliseLog:
    counts: Counter = field(default_factory=Counter)
    figures: List[Figure] = field(default_factory=list)
    duplicate_notes: List[int] = field(default_factory=list)
    unmatched_markers: List[int] = field(
        default_factory=list
    )  # in the text, never defined
    unused_notes: List[int] = field(default_factory=list)  # defined, never cited
    # Everything taken out of the text, so the removal is auditable (provenance): the repeated
    # definition of a note, a map plate's legend, the model's description of a picture.
    dropped: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "counts": dict(self.counts),
            "figures": [asdict(f) for f in self.figures],
            "duplicate_notes": self.duplicate_notes,
            "unmatched_markers": self.unmatched_markers,
            "unused_notes": self.unused_notes,
            "dropped": self.dropped,
        }


@dataclass
class _Para:
    text: str
    page: Optional[int]
    kind: str  # anchor | rule | body | heading | caption | note | other
    note: int = 0
    before_notes: bool = (
        False  # the next paragraph on its page is a note or the notes' rule
    )


def normalise_chandra_chapter(
    markdown: str, vocab: Optional[Counter] = None
) -> Tuple[str, NormaliseLog]:
    """``markdown`` (one chapter, ``<a id="pageN"></a>`` page anchors) in parser form, with its
    log. ``vocab`` is the book's word counts (src/ingestion/ocr_sanitise.vocabulary), used to
    keep a hyphen the book prints; without it a hyphen at a break is always removed."""
    log = NormaliseLog()
    state = _Assembly(vocab, log)
    for p in _paragraphs(markdown, log):
        state.take(p)
    return _render(state.finish(), state.notes, log), log


class _Assembly:
    """The chapter as it is put back together: the narrative, the notes, and what is still open
    (a paragraph or note cut off by a break, anchors not yet placed)."""

    def __init__(self, vocab: Optional[Counter], log: NormaliseLog):
        self.vocab, self.log = vocab, log
        self.notes: Dict[int, str] = {}
        self.body: List[_Para] = []
        self.open_body: Optional[_Para] = None
        self.open_note: Optional[int] = None
        self.open_note_page: Optional[int] = None  # the page the open note last grew on
        self.dropping = False  # the previous paragraph was (part of) a repeated note
        self.drop_page: Optional[int] = None  # the page the repeated note is on
        self.pending: List[int] = []  # pages whose anchor is not yet placed
        self.last_note: Optional[int] = (
            None  # the note the previous paragraph belonged to
        )
        self.after_rule = (
            False  # the previous paragraph was the rule above a page's notes
        )

    def take(self, p: _Para) -> None:
        if p.kind == "anchor":
            self.pending.append(p.page)  # type: ignore[arg-type]
            self.last_note, self.after_rule = None, False
        elif p.kind == "rule":
            self.after_rule = True
        elif p.kind == "note":
            repeated = p.note in self.notes
            self.open_note = _add_note(self.notes, p, self.log)
            self.open_note_page = p.page
            # a repeated definition is dropped (logged), and so is whatever continues it
            self.last_note = None if repeated else p.note
            self.dropping, self.drop_page = repeated, p.page
            self.after_rule = False
        elif self._drops(p):
            return
        elif not self._into_note(p) and not self._continues(p):
            self._place(p)

    def _drops(self, p: _Para) -> bool:
        """A paragraph that continues a repeated (dropped) note is dropped with it, logged: the
        lower-case rest of its sentence, or a further complete paragraph before the next note.
        """
        if not self.dropping:
            return False
        if p.kind != "body" or p.page != self.drop_page or self.after_rule:
            self.dropping = False
            return False
        if not (p.text[:1].islower() or _between_notes(p)):
            self.dropping = False
            return False
        self.log.dropped.append(
            {"page": p.page, "kind": "repeated_note_continuation", "text": p.text}
        )
        return True

    def _into_note(self, p: _Para) -> bool:
        target = _note_paragraph_of(
            p, self.last_note, self._open_note_near(p), self.after_rule
        )
        self.after_rule = False
        self.last_note = target
        if target is None:
            return False
        self._extend_note(target, p.text)
        self.open_note_page = p.page
        self.log.counts["note_paragraph_between_notes"] += 1
        return True

    def _continues(self, p: _Para) -> bool:
        """A lower-case paragraph continues the open paragraph or the open note."""
        if p.kind != "body" or not p.text[:1].islower():
            return False
        note = self._open_note_near(p)
        if note is not None and self._fragment_of_note(note, p):
            self._extend_note(note, p.text)
            self.open_note_page = p.page
            return True
        if self.open_body is not None:
            if note is not None:
                self.log.counts[
                    "continuation_ambiguous"
                ] += 1  # body chosen over the note
            _join(self.open_body, p, self.pending, self.vocab, self.log)
            self.pending = []
            if _complete(self.open_body.text):
                self.open_body = None
            return True
        self.log.counts["lowercase_unjoined"] += 1
        return False

    def _fragment_of_note(self, number: int, p: _Para) -> bool:
        """``p`` (lower case) continues note ``number``: the note stops mid-word and the open
        paragraph does not, or ``p`` follows the note on its own page with no paragraph open.
        A fragment on a later page after a note that ended without a stop is not its run-over
        (those come after the rule: ``_note_paragraph_of``)."""
        if self._note_is_cut(number):
            return True
        same_page = p.page is None or self.open_note_page in (None, p.page)
        return same_page and self.open_body is None

    def _note_is_cut(self, number: int) -> bool:
        """The note stops mid-word ("suc-"), and the open paragraph does not: its run-over is
        the next lower-case fragment, not the paragraph's."""
        body_cut = self.open_body is not None and self.open_body.text.rstrip().endswith(
            "-"
        )
        return self.notes[number].rstrip().endswith("-") and not body_cut

    def _open_note_near(self, p: _Para) -> Optional[int]:
        """The open note, if ``p`` is on its page or the next: only then can its run-over be
        here. A note that ended without a full stop ("Ibid., p. 45") pages ago is not open.
        """
        if self.open_note is None:
            return None
        if p.page is None or self.open_note_page is None:
            return self.open_note
        return self.open_note if 0 <= p.page - self.open_note_page <= 1 else None

    def _extend_note(self, number: int, text: str) -> None:
        self.notes[number] = _continued_note(
            self.notes[number], text, self.vocab, self.log
        )
        self.open_note = None if _complete(self.notes[number]) else number

    def _place(self, p: _Para) -> None:
        self._flush_anchors()
        self.body.append(p)
        if p.kind == "body":
            self.open_body = None if _complete(p.text) else p
        elif p.kind == "heading":
            self.open_body = None

    def _flush_anchors(self) -> None:
        self.body += [_Para(f'<a id="page{n}"></a>', n, "other") for n in self.pending]
        self.pending = []

    def finish(self) -> List[_Para]:
        self._flush_anchors()
        return self.body


def _paragraphs(markdown: str, log: NormaliseLog) -> List[_Para]:
    """Paragraphs in order, with the page anchors as their own items."""
    out: List[_Para] = []
    page: Optional[int] = None
    for raw in _PARAGRAPH_BREAK.split(markdown):
        for piece in _split_anchors(raw):
            text = piece.strip()
            if not text:
                continue
            anchor = _ANCHOR.fullmatch(text)
            if anchor:
                page = int(anchor.group(1))
                out.append(_Para(text, page, "anchor"))
                continue
            kind = _kind(text, page, log)
            if kind is None:
                continue
            note = _NOTE_DEF.match(text)
            out.append(_Para(text, page, kind, int(note.group(1)) if note else 0))
    for here, after in zip(out, out[1:]):
        here.before_notes = after.kind in ("note", "rule")
    return _drop_map_text(out, log)


# Chandra's alt text of a map plate OPENS with the map ("A historical map of Europe titled ...",
# "Map of the Cotentin ..."): a picture merely containing one ("Soldiers studying a map on a
# jeep") is not a map plate.
_NOT_ADJECTIVE = r"(?:an?|the|of|on|in|at|by|with|from|to|for)\b"
_MAP_ALT = re.compile(
    r"^\W*(?:an?\s+|the\s+)?(?:(?!"
    + _NOT_ADJECTIVE
    + r")[a-z][a-z-]*\s+){0,4}?(?:maps?|charts?)\b"
    r"(?=\s+(?:of|showing|titled|depicting|illustrating|displaying|with|that|used|from|"
    r"labeled|labelled|marked|\d)\b|\s*[,.:])",
    re.I,
)
_NARRATIVE_WORDS = 30  # a paragraph this long is the book's prose, not a map's legend
_SENTENCE_WORDS = 6  # a complete sentence this long is prose too


def _is_prose(text: str) -> bool:
    """Mostly lower-case words, and either long or a complete sentence: prose, not a list of
    place labels or a legend."""
    words = re.findall(r"[^\W\d_]+", re.sub(r"<[^>]+>", " ", text))
    if sum(1 for w in words if w.islower()) < len(words) / 2:
        return False
    return len(words) >= _NARRATIVE_WORDS or (
        len(words) >= _SENTENCE_WORDS and text.rstrip().endswith(_TERMINAL)
    )


def _drop_map_text(paras: List[_Para], log: NormaliseLog) -> List[_Para]:
    """A map plate's OCR is its legend, scale and place labels (and Chandra's own "Labels on
    map: ..." list), not narrative: on a page holding a map image and no prose paragraph, the
    other paragraphs are dropped and the map's printed title is kept with its figure."""
    by_page: Dict[Optional[int], List[_Para]] = {}
    for p in paras:
        by_page.setdefault(p.page, []).append(p)
    drop: set = set()
    for page, items in by_page.items():
        drop.update(id(p) for p in _map_text(page, items, log))
    return [p for p in paras if id(p) not in drop]


def _map_text(
    page: Optional[int], items: List[_Para], log: NormaliseLog
) -> List[_Para]:
    """The paragraphs of ``page`` that are a map plate's text (none on a page with prose)."""
    # only the plate's own text: notes and headings are the book's, whatever the page holds
    texts = [p for p in items if p.kind in ("body", "caption", "other")]
    plate = _plate_figure(page, log)
    if plate is None or not texts or any(_is_prose(p.text) for p in texts):
        return []
    plate.printed_title = re.sub(r"[*_]+", "", texts[0].text).replace("\n", " ").strip()
    log.counts["map_text_removed"] += len(texts)
    log.dropped += [{"page": page, "kind": "map_text", "text": p.text} for p in texts]
    return texts


def _plate_figure(page: Optional[int], log: NormaliseLog) -> Optional[Figure]:
    """The map on ``page``: a figure whose model-written description opens with the map."""
    return next(
        (
            f
            for f in log.figures
            if f.page == page and _MAP_ALT.search(f.model_description)
        ),
        None,
    )


def _split_anchors(raw: str) -> List[str]:
    """A paragraph holding a page anchor on its own line is split around it."""
    pieces, last = [], 0
    for m in _ANCHOR.finditer(raw):
        pieces += [raw[last : m.start()], m.group(0)]
        last = m.end()
    pieces.append(raw[last:])
    return pieces


def _kind(text: str, page: Optional[int], log: NormaliseLog) -> Optional[str]:
    """rule | body | heading | caption | note | other, or None for a dropped paragraph."""
    image = _IMAGE.match(text)
    if image:
        for alt, file in re.findall(r"!\[([^\]]{0,2000})\]\(([^)\s]{1,500})\)", text):
            log.figures.append(Figure(page, file, alt))
        log.counts["image_paragraph_removed"] += 1
        log.dropped.append({"page": page, "kind": "image_description", "text": text})
        return None
    if _RULE.match(text):
        log.counts["rule_removed"] += 1  # Chandra's rule above a page's notes
        return "rule"
    if _NOTE_DEF.match(text):
        return "note"
    if _HEADING.match(text):
        return "heading"
    if is_chandra_caption(text):
        log.counts["caption"] += 1
        return "caption"
    if text.startswith(("<table", "|", "<")):
        return "other"
    return "body"


def _add_note(notes: Dict[int, str], p: _Para, log: NormaliseLog) -> Optional[int]:
    m = _NOTE_DEF.match(p.text)
    number, text = p.note, (m.group(2).strip() if m else "")
    if number in notes:
        log.duplicate_notes.append(number)
        log.dropped.append(
            {"page": p.page, "kind": "repeated_note", "number": number, "text": text}
        )
        return (
            None  # its continuation (if any) must not extend the kept first definition
        )
    notes[number] = text
    log.counts["note"] += 1
    return None if _complete(text) else number


def _between_notes(p: _Para) -> bool:
    """A complete upper-case paragraph with no note marker of its own, followed by a note (or
    the rule above the next notes): the next paragraph of the note before it."""
    return (
        p.before_notes
        and p.text[:1].isupper()
        and _complete(p.text)
        and not _NOTE_MARK.search(p.text)
    )


def _note_paragraph_of(
    p: _Para, last_note: Optional[int], open_note: Optional[int], after_rule: bool
) -> Optional[int]:
    """The note ``p`` belongs to, if it is part of one rather than of the narrative:

    * the first paragraph under the rule above a page's notes, opening lower-case, while a note
      is still open - the note run over from the previous page (CXA note 68: "... with" / rule /
      "heavy weapons and a local reserve ...");
    * a paragraph opening upper-case between a note and the next note (or the rule above the
      next notes) of its page, with no note marker of its own - the note's next paragraph (CXA
      note 65: Gerow's career, then "General Huebner enlisted in 1910 ...", then note 66) and
      ending complete.
      Chandra also sets narrative after a page's notes (CXA note 77, then "Already, however, as
      Talley sent forward ..." and more narrative): with no note after it, it stays narrative.
    """
    if p.kind != "body":
        return None
    if after_rule and open_note is not None and p.text[:1].islower():
        return open_note
    if last_note is not None and _between_notes(p):
        return last_note
    return None


def _continued_note(
    note: str, text: str, vocab: Optional[Counter], log: NormaliseLog
) -> str:
    log.counts["note_continued"] += 1
    if note and not _complete(note):
        return _joined_text(note, text, vocab, log)
    return (
        f"{note} {text}".strip()
    )  # a further paragraph of the note (kept on one line)


def _complete(text: str) -> bool:
    t = _NOTE_MARK.sub("", text).rstrip()
    t = re.sub(r"[*_]+$", "", t).rstrip()  # closing emphasis
    return not t or t.endswith(_TERMINAL)


def _join(
    head: _Para,
    tail: _Para,
    anchors: List[int],
    vocab: Optional[Counter],
    log: NormaliseLog,
) -> None:
    marker = "".join(f'<a id="page{n}"></a>' for n in anchors)
    head.text = _joined_text(head.text, tail.text, vocab, log, marker)
    log.counts["paragraph_rejoined"] += 1


def _joined_text(
    head: str, tail: str, vocab: Optional[Counter], log: NormaliseLog, marker: str = ""
) -> str:
    """``head`` continued by ``tail`` across a break; a hyphen at the break is the line's, not
    the word's, unless the book prints the hyphenated form at least as often as the joined.
    """
    end, start = _WORD_END.search(head), _WORD_START.match(tail)
    if end and start:
        joined = (end.group(1) + start.group(1)).lower()
        hyphenated = f"{end.group(1)}-{start.group(1)}".lower()
        counts = vocab or Counter()
        if counts[hyphenated] and counts[hyphenated] >= counts[joined]:
            log.counts["hyphen_kept"] += 1
            return f"{head}{marker}{tail}"
        log.counts["hyphen_removed"] += 1
        return f"{head[:-1]}{marker}{tail}"
    return f"{head.rstrip()} {marker}{tail}" if marker else f"{head.rstrip()} {tail}"


def _render(body: List[_Para], notes: Dict[int, str], log: NormaliseLog) -> str:
    cited: set = set()

    def marker(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        if n not in notes:
            log.unmatched_markers.append(n)
            return m.group(0)
        cited.add(n)
        return f'<sup><a href="#fn{n}" id="cn{n}">{n}</a></sup>'

    parts = [_NOTE_MARK.sub(marker, p.text) for p in body]
    log.unused_notes = sorted(set(notes) - cited)
    log.unmatched_markers = sorted(set(log.unmatched_markers))
    if notes:
        parts.append("### Footnotes")
        parts += [
            f'\\[<a href="#cn{n}" id="fn{n}">{n}</a>\\] {_one_line(notes[n])}'
            for n in sorted(notes)
        ]
    return "\n\n".join(parts) + "\n"


def _one_line(text: str) -> str:
    """A note's text on one line: a blank line inside it would end the definition early."""
    return re.sub(r"\s*\n\s*", " ", text).strip()
