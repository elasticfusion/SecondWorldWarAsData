"""Reference-free sanitising of OCR markdown (Chandra), run on EVERY OCR'd document.

The PDF <-> HTML cross-check (src/ingestion/pdf_html_crosscheck) corrects OCR artefacts only
where a transcription exists to compare against; a document without a validation URL is never
cross-checked against nothing. Some artefacts need no transcription to settle, because the
document itself does - they are fixed here for every document:

* **homoglyphs** - Chandra reads small capitals as Cyrillic / Greek look-alikes ("УТАН" for
  UTAH, "ОМАНА" for OMAHA). A word made only of Latin letters and look-alikes is written in
  Latin letters, each letter's case kept - but only when the document itself prints that Latin
  word elsewhere (its own vocabulary), and never on a page whose text is largely Cyrillic or
  Greek (a real Russian or Greek page). A look-alike word the document does not attest, or one
  with a Cyrillic/Greek letter that has no Latin look-alike ("УГАН": "Г"), is left as read and
  listed for review.
* **superscript ordinals** - Chandra sets an ordinal's suffix as a superscript
  ("709<sup>th</sup>"), which the parser could take for a note marker. It is joined
  ("709th") only when the suffix agrees with the number (the Army's "2d"/"3d" included), so a
  lettered note after a year ("1944<sup>d</sup>") is left alone.

Every change and every unresolved item is logged with its physical PDF page, so the sanitised
text is reproducible from the raw OCR (which the merge keeps) and reversible. Deterministic, no
model calls, no AWS.

Scope: the rules assume a Latin-script document (docs/current/TODO.md: per-language rule sets).
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from src.ingestion.chunk_pages import PAGE_SEPARATOR_RE
from src.ingestion.pdf_html_crosscheck import HOMOGLYPH_CHARS, suffix_agrees
from src.ingestion.pdf_text_corrections import to_latin

_WORD = re.compile(r"[^\W\d_]+")
_ORDINAL_SUP = re.compile(
    r"(?<![\d.,])(?P<number>\d{1,5})<sup>\s{0,2}(?P<suffix>st|nd|rd|th|d)\s{0,2}</sup>",
    re.IGNORECASE,
)
# A page on which more than this share of letters is Cyrillic/Greek is real Cyrillic/Greek text,
# not an English page with a few small-caps look-alikes - given enough letters to judge (a page
# holding only a small-caps heading, "УТАН BEACH", is not a Russian page).
_FOREIGN_PAGE_SHARE = 0.3
_FOREIGN_MIN_LETTERS = 50
_MIN_WORD = (
    3  # "Нам" is a Russian word whichever Latin look-alike the book happens to print
)
_CONTEXT = 30  # characters either side searched for real Cyrillic/Greek letters
# Years 1500-2099: "1942<sup>d</sup>" is a year with a lettered note, never the ordinal "1942d".
_YEAR = range(1500, 2100)


@dataclass
class Change:
    page: Optional[
        int
    ]  # physical PDF page (1-based), when the chunk's first page is known
    rule: str  # homoglyph | ordinal
    original: str
    corrected: str
    evidence: Dict[str, int] = field(default_factory=dict)


@dataclass
class Unresolved:
    page: Optional[int]
    rule: str
    text: str
    reason: str  # not_attested | no_latin_lookalike


@dataclass
class SanitiseLog:
    changes: List[Change] = field(default_factory=list)
    unresolved: List[Unresolved] = field(default_factory=list)
    skipped_foreign_pages: List[Optional[int]] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        by_rule = Counter(c.rule for c in self.changes)
        return {
            "homoglyph": by_rule["homoglyph"],
            "ordinal": by_rule["ordinal"],
            "unresolved": len(self.unresolved),
            "foreign_pages_skipped": len(self.skipped_foreign_pages),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "changes": [asdict(c) for c in self.changes],
            "unresolved": [asdict(u) for u in self.unresolved],
            "skipped_foreign_pages": self.skipped_foreign_pages,
        }


def vocabulary(texts: Sequence[str]) -> Counter:
    """Lower-cased counts of the purely Latin-letter words of ``texts`` (the document's own
    attested spellings; a look-alike word does not attest itself)."""
    vocab: Counter = Counter()
    for text in texts:
        for m in _WORD.finditer(text):
            w = m.group(0)
            if w.isascii():
                vocab[w.lower()] += 1
    return vocab


def sanitise_parts(
    parts: Sequence[Tuple[Optional[int], str]],
) -> Tuple[List[str], SanitiseLog]:
    """Sanitise each ``(first_physical_page, markdown)`` OCR part (one per chunk; the page is
    None when unknown) against the vocabulary of ALL parts. Returns the texts and the log.
    """
    vocab = vocabulary([text for _, text in parts])
    log = SanitiseLog()
    return [sanitise(text, first, vocab, log) for first, text in parts], log


def sanitise(
    markdown: str, first_page: Optional[int], vocab: Counter, log: SanitiseLog
) -> str:
    """``markdown`` with homoglyph words and superscript ordinals fixed; appends to ``log``."""
    pages = _Pages(markdown, first_page)
    foreign = pages.foreign_pages(markdown)
    log.skipped_foreign_pages += [pages.number(i) for i in sorted(foreign)]
    out = _ORDINAL_SUP.sub(lambda m: _ordinal(m, pages, log), markdown)
    pages = _Pages(out, first_page)  # offsets moved
    return _WORD.sub(lambda m: _homoglyph(m, pages, foreign, vocab, log), out)


def _ordinal(m: "re.Match[str]", pages: "_Pages", log: SanitiseLog) -> str:
    number, suffix = m.group("number"), m.group("suffix")
    if not suffix_agrees(number, suffix.lower()):
        return m.group(0)
    if int(number) in _YEAR:
        log.unresolved.append(
            Unresolved(
                pages.number(pages.index(m.start())), "ordinal", m.group(0), "year"
            )
        )
        return m.group(0)
    joined = number + suffix
    log.changes.append(
        Change(pages.number(pages.index(m.start())), "ordinal", m.group(0), joined)
    )
    return joined


def _homoglyph(
    m: "re.Match[str]",
    pages: "_Pages",
    foreign: set,
    vocab: Counter,
    log: SanitiseLog,
) -> str:
    word = m.group(0)
    index = pages.index(m.start())
    if not _maybe_small_caps(m, index in foreign):
        return word
    page = pages.number(index)
    if not all(ch.isascii() or ch.lower() in HOMOGLYPH_CHARS for ch in word):
        return _unresolved(log, page, word, "no_latin_lookalike")
    latin = to_latin(word)
    seen = vocab[latin.lower()]
    if not seen:
        return _unresolved(log, page, word, "not_attested")
    log.changes.append(Change(page, "homoglyph", word, latin, {"book": seen}))
    return latin


def _maybe_small_caps(m: "re.Match[str]", foreign_page: bool) -> bool:
    """The word may be small capitals read as look-alike letters: not plain Latin, not real
    Cyrillic/Greek with no look-alike letter, not on a Cyrillic/Greek page, not a short word,
    and not among real Cyrillic/Greek letters."""
    word = m.group(0)
    if word.isascii() or not any(ch.lower() in HOMOGLYPH_CHARS for ch in word):
        return False
    return not (foreign_page or len(word) < _MIN_WORD or _foreign_context(m))


def _unresolved(log: SanitiseLog, page: Optional[int], word: str, reason: str) -> str:
    log.unresolved.append(Unresolved(page, "homoglyph", word, reason))
    return word


def _foreign_context(m: "re.Match[str]") -> bool:
    """Real Cyrillic/Greek letters (ones with no Latin look-alike) next to the word - not in it:
    a word holding one is reported as unresolved, not skipped silently."""
    near = (
        m.string[max(0, m.start() - _CONTEXT) : m.start()]
        + m.string[m.end() : m.end() + _CONTEXT]
    )
    return any(
        _is_cyrillic_or_greek(ch) and ch.lower() not in HOMOGLYPH_CHARS for ch in near
    )


class _Pages:
    """Page lookup by character offset in one chunk's paginated markdown."""

    def __init__(self, markdown: str, first_page: Optional[int]):
        self.first = first_page
        self.starts = [0] + [m.end() for m in PAGE_SEPARATOR_RE.finditer(markdown)]

    def index(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset) - 1

    def number(self, index: int) -> Optional[int]:
        return None if self.first is None else self.first + index

    def foreign_pages(self, markdown: str) -> set:
        """Indexes of pages whose letters are largely Cyrillic/Greek."""
        latin: Counter = Counter()
        other: Counter = Counter()
        for m in _WORD.finditer(markdown):
            i = self.index(m.start())
            for ch in m.group(0):
                if ch.isascii():
                    latin[i] += 1
                elif _is_cyrillic_or_greek(ch):
                    other[i] += 1
        return {
            i
            for i, n in other.items()
            if n >= _FOREIGN_MIN_LETTERS and n / (n + latin[i]) > _FOREIGN_PAGE_SHARE
        }


def _is_cyrillic_or_greek(ch: str) -> bool:
    return "\u0370" <= ch <= "\u03ff" or "\u0400" <= ch <= "\u04ff"
