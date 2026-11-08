"""Deterministic corrections of an AUTHORITATIVE scanned PDF's OCR text (Chandra markdown), from a
PDF <-> transcription cross-check (src/ingestion/pdf_html_crosscheck).

Owner rules: the PDF is the source of record, and where printings differ the NEWER one is
deferred to - especially for people's names. The CMH PDF is the 1993 printing (CMH Pub 7-4-1);
HyperWar transcribes the 1951 one (its map list still has the Rand McNally world map as Map I, so
every later map number is one higher). So the PDF's OCR artefacts are corrected only where the
book itself settles them, and only to a reading the 1993 edition itself prints. Only the
cross-check's CORRECTABLE classes change the text, each to the reading the book attests:

* ``ocr_spacing``   -> the form the WHOLE book prints ("counter-attack" -> "counterattack",
  "fortyeight" -> "forty-eight"), built from the PDF's own letters - not the transcription's form;
* ``ocr_homoglyph`` -> the PDF's own letters in Latin script, case kept ("УТАН" -> "UTAH");
* ``ocr_confusion`` / ``ocr_frequency`` / ``ocr_crossread`` -> the ONE word the book (or a second
  OCR of the same pages) establishes ("Schwarzwaldor" -> "Schwarzwalder").

Everything else is left exactly as the OCR read it: transcription errors (``html_error``) and the
book's own forms (``book_form``, ``html_accent``) need nothing, and ``review`` items wait for a
human. A correction is skipped - counted, never guessed - when the reading has no single position
in the markdown, markup or a paragraph break sits inside it, it spans several words (their
hyphens, commas and apostrophes are the PDF's and must not be rewritten from the transcription),
or the newer edition does not print the reading. Every applied change is logged with its page,
class and the counts behind it, so the corrected text is reproducible from the OCR and
reversible.

Scope: the rules are for an ENGLISH book in Latin script (docs/current/TODO.md: per-language
rule sets). A homoglyph fix only fires where the transcription has the Latin word at the same
place, but a Russian, French, German, Dutch or Japanese document needs its own rules (Japanese
its own word segmentation) before it is corrected.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from src.ingestion import pdf_html_crosscheck as X

# Between the words of one reading only white space, hyphens and digit-group commas may stand;
# anything else (markup, a footnote marker) means the reading is not one plain run of text.
_BETWEEN_WORDS = re.compile(r"[\s\-,]*")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass
class Correction:
    pdf_page: Optional[int]
    printed_page: Optional[int]
    cls: str
    original: str  # the source markdown replaced (span)
    corrected: str
    span: Tuple[int, int]
    evidence: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass
class Plan:
    """The corrections to apply, and what was skipped: counts by reason, and the skipped
    differences themselves as (class, HTML, PDF) - what a re-check must still find."""

    corrections: List[Correction] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    skipped_differences: List[Tuple[str, str, str]] = field(default_factory=list)

    def skip(self, reason: str, d: X.Difference) -> None:
        self.skipped[reason] += 1
        self.skipped_differences.append((d.cls, d.html, d.pdf))


def plan_corrections(
    checks: Sequence[X.CrossCheck],
    markdown: str,
    vocab: Counter,
    second: Optional[Counter] = None,
) -> Plan:
    """The corrections of ``markdown``, the Chandra text the cross-check read (its Differences
    carry spans into it), in source order."""
    plan = Plan()
    found: List[Tuple[Correction, X.Difference]] = []
    for check in checks:
        for d in check.differences:
            if d.cls not in X.CORRECTABLE:
                continue
            fix = _correction(d, markdown, vocab, second)
            if isinstance(fix, str):
                plan.skip(fix, d)
            else:
                found.append((fix, d))
    for fix, d in sorted(found, key=lambda pair: pair[0].span):
        if plan.corrections and fix.span[0] < plan.corrections[-1].span[1]:
            plan.skip("overlap", d)
        else:
            plan.corrections.append(fix)
    return plan


def _correction(
    d: X.Difference, markdown: str, vocab: Counter, second: Optional[Counter]
) -> "Correction | str":
    """The correction of one difference, or the reason it is skipped."""
    reason = _unplaceable(d, markdown)
    if reason:
        return reason
    original = markdown[d.span[0] : d.span[1]]  # type: ignore[index]
    corrected = _corrected(d, original, vocab)
    if isinstance(corrected, Skip):
        return corrected.reason
    if d.cls != "ocr_homoglyph" and not _attested(corrected, vocab, second):
        # the transcription may follow an older printing ("Map VIII" in 1951 is "Map VII" in
        # the 1993 CMH printing): never write a reading - above all a name - the newer edition
        # does not itself print. (A homoglyph fix writes the PDF's own letters in Latin script.)
        return "not_in_newer_edition"
    return Correction(
        d.pdf_page, d.printed_page, d.cls, original, corrected, d.span,  # type: ignore[arg-type]
        _evidence(d, vocab, second),
    )  # fmt: skip


@dataclass
class Skip:
    reason: str


def _unplaceable(d: X.Difference, markdown: str) -> Optional[str]:
    """Why the PDF reading cannot be replaced in place, if it cannot."""
    if d.span is None:
        return "no_position"  # a word joined across a break, or normalised text
    original = markdown[d.span[0] : d.span[1]]
    if X.words(original) != d.pdf.split():
        return "position_mismatch"
    if _PARAGRAPH_BREAK.search(original):
        return "paragraph_break_inside"
    if not _BETWEEN_WORDS.fullmatch(X._WORD.sub("", X._normalised(original))):
        return "markup_inside"
    return None


def _corrected(d: X.Difference, original: str, vocab: Counter) -> "str | Skip":
    if d.cls == "ocr_spacing":
        if "," in original:
            return Skip(
                "comma_inside"
            )  # digit grouping or a list: the PDF's, never joined
        spaced = _book_spacing(d, vocab)
        return _like(original, spaced) if spaced else Skip("no_established_form")
    if len(d.pdf.split()) != 1 or len(d.html.split()) != 1:
        return Skip("multi_word")
    if d.cls == "ocr_homoglyph":
        return _latin(original)
    return _like(original, d.html)


def _latin(word: str) -> str:
    """Cyrillic / Greek look-alike letters as Latin, each letter's case kept ("Утан" -> "Utah")."""
    out = []
    for ch in word:
        lower = ch.lower().translate(X._HOMOGLYPH_U).translate(X._HOMOGLYPHS)
        out.append(lower.upper() if ch.isupper() else lower)
    return "".join(out)


def _like(original: str, reading: str) -> str:
    """``reading`` in the case style of ``original`` (an ALL-CAPS heading stays ALL-CAPS) and
    with its typographic apostrophe."""
    if original.isupper() and len(original) > 1:
        reading = reading.upper()
    return reading.replace("'", "’") if "’" in original else reading


def _attested(reading: str, vocab: Counter, second: Optional[Counter]) -> bool:
    """Every word of ``reading`` occurs in the PDF's own text: its whole-book vocabulary or the
    second OCR of the same pages."""
    seen = second or Counter()
    found = X.words(reading)
    return bool(found) and all(vocab[w.lower()] or seen[w.lower()] for w in found)


def _book_spacing(d: X.Difference, vocab: Counter) -> Optional[str]:
    """The PDF's letters in the form the book prints: the PDF's own pieces when it split the
    word, else its word cut where the transcription's pieces fall ("fortyeight" + "forty" /
    "eight" -> "forty" / "eight"). When both sides split it, the PDF's pieces are kept.
    """
    pdf_words, html_words = d.pdf.split(), d.html.split()
    pieces = pdf_words if len(pdf_words) > 1 else _cut(pdf_words[0], html_words)
    form = X.established_form(pieces, vocab) if pieces else None
    if form is None:
        return None
    return ("-" if form == "hyphen" else "").join(pieces)


def _cut(word: str, like: Sequence[str]) -> List[str]:
    """``word`` cut into pieces the lengths of ``like``; [] if the lengths do not add up."""
    if sum(len(p) for p in like) != len(word):
        return []
    out, pos = [], 0
    for piece in like:
        out.append(word[pos : pos + len(piece)])
        pos += len(piece)
    return out


def _evidence(
    d: X.Difference, vocab: Counter, second: Optional[Counter]
) -> Dict[str, Dict[str, int]]:
    """Whole-book counts (and second-OCR counts) of both readings: why the change was made. For
    a spacing fix, the joined and the hyphenated form ("counterattack" 88 / "counter-attack" 0).
    """
    if d.cls == "ocr_spacing":
        pieces = (d.pdf if len(d.pdf.split()) > 1 else d.html).lower().split()
        keys = ["".join(pieces), "-".join(pieces)]
    else:
        keys = [d.html.lower(), d.pdf.lower()]
    keys = list(dict.fromkeys(keys))
    out = {"book": {k: vocab[k] for k in keys}}
    if second is not None:
        out["second_ocr"] = {k: second[k] for k in keys}
    return out


def apply_corrections(markdown: str, corrections: Sequence[Correction]) -> str:
    """``markdown`` with each correction's span replaced (non-overlapping, in source order)."""
    parts, pos = [], 0
    for c in sorted(corrections, key=lambda c: c.span):
        if c.span[0] < pos or markdown[c.span[0] : c.span[1]] != c.original:
            raise ValueError(
                f"correction does not fit the text at {c.span}: {c.original!r}"
            )
        parts += [markdown[pos : c.span[0]], c.corrected]
        pos = c.span[1]
    parts.append(markdown[pos:])
    return "".join(parts)
