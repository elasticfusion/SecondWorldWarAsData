"""Find a quoted passage in source paragraphs - the ONE matcher for "is this quote in the book?".

Used to resolve an anchor quote to its paragraph (``src/extraction/anchor_resolve``) and to verify a
record's quoted provenance (``scripts/validate_provenance``). A quote either appears in the source
text or it does not: an exact match (after normalising both sides) proves it; a near match (OCR
noise, a model's small rewording) is reported as such, never as proof.

Normalising strips what the parsed markdown carries and a quote does not: footnote markers
(``<sup>[1](url)</sup>``), images, link targets, emphasis (``_Seventh Army,_``) and page markers
(``_\\--212--_``); then case, punctuation and whitespace.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Minimum similarity for a near match to be reported (below -> not found).
FUZZY_THRESHOLD = 0.85
# A near match needs this many words (a 2-3 word quote "matches" half the book) ...
MIN_FUZZY_WORDS = 4
# ... and must beat the next-best paragraph by this much (else it is a guess).
FUZZY_MARGIN = 0.05
# A quote of fewer words proves little: found, it passes; not found, it is only a warning.
MIN_VERIFY_WORDS = 3

EXACT = "exact"
NEAR = "near"

_SUP = re.compile(r"<sup>.*?</sup>", re.IGNORECASE | re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_PAGE_MARK = re.compile(r"_?\\?-{2,}\s*\d+\s*-{2,}_?")
_TAG = re.compile(r"<[^>]+>")
# "_" too: markdown emphasis, not a word character here
_PUNCT = re.compile(r"[^\w\s]|_")
_ELISION = re.compile(r"\.\s*\.\s*\.|\u2026")
# UTF-8 text decoded as cp1252/latin-1 ("la FiÃ¨re", "â€™")
_MOJIBAKE = ("\u00c3", "\u00e2\u20ac")


def _unmangle(t: str) -> str:
    """Undo UTF-8 text decoded as cp1252/latin-1 ("la FiÃ¨re" -> "la Fière"); else unchanged."""
    if not any(m in t for m in _MOJIBAKE):
        return t
    for enc in ("cp1252", "latin-1"):
        try:
            return t.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return t


def normalize(text: Any) -> str:
    """Markup removed, casefolded, punctuation -> space, whitespace collapsed. Pure."""
    t = unicodedata.normalize("NFKC", _unmangle(str(text or "")))
    t = _SUP.sub(" ", t)
    t = _IMAGE.sub(" ", t)
    t = _LINK.sub(r"\1", t)
    t = _PAGE_MARK.sub(" ", t)
    t = _TAG.sub(" ", t)
    t = _PUNCT.sub(" ", t.casefold())
    return " ".join(t.split())


Index = List[Tuple[int, str]]  # (paragraph absolute_number, normalised text)


def build_index(paragraphs: Iterable[Dict[str, Any]]) -> Index:
    """(absolute_number, normalised text) per parsed paragraph; malformed entries skipped."""
    index: Index = []
    for p in paragraphs or []:
        if not isinstance(p, dict):
            continue
        try:
            num = int(p.get("absolute_number"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        index.append((num, normalize(p.get("text", ""))))
    return index


def _word_ratio(quote: str, text: str) -> float:
    """Share of the quote's words found IN ORDER, within a stretch of ``text`` at most twice the
    quote's length (one word-level alignment per paragraph: a short quote inside a long
    paragraph is scored against itself, not against the whole paragraph)."""
    q, t = quote.split(), text.split()
    if not q or not t:
        return 0.0
    matcher = SequenceMatcher(None, q, t, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size]
    best = 0
    for i, first in enumerate(blocks):  # the densest in-order run within the span limit
        matched = 0
        for b in blocks[i:]:
            if b.b + b.size - first.b > 2 * len(q):
                break
            matched += b.size
        best = max(best, matched)
    return best / len(q)


def _exact(norm_quote: str, index: Index) -> Optional[int]:
    """The first paragraph holding the quote as whole words."""
    padded = f" {norm_quote} "
    for num, text in index:
        if padded in f" {text} ":
            return num
    return None


def _near(norm_quote: str, index: Index) -> Optional[int]:
    """The paragraph most like the quote, if at or above ``FUZZY_THRESHOLD`` and clear of the
    runner-up by ``FUZZY_MARGIN`` (two about-equal paragraphs: ambiguous, no answer)."""
    scored = sorted(((_word_ratio(norm_quote, t), n) for n, t in index), reverse=True)
    if not scored or scored[0][0] < FUZZY_THRESHOLD:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < FUZZY_MARGIN:
        return None
    return scored[0][1]


def find_quote(
    norm_quote: str, index: Index, fuzzy: bool = True
) -> Tuple[Optional[int], str]:
    """(the paragraph holding ``norm_quote`` | None, ``EXACT`` | ``NEAR`` | "").

    Exact (whole words of a paragraph) first, in paragraph order; then, for a quote of at least
    ``MIN_FUZZY_WORDS``, a near match (``_near``). Never guesses.
    """
    if not norm_quote:
        return None, ""
    num = _exact(norm_quote, index)
    if num is not None:
        return num, EXACT
    if not fuzzy or len(norm_quote.split()) < MIN_FUZZY_WORDS:
        return None, ""
    num = _near(norm_quote, index)
    return (num, NEAR) if num is not None else (None, "")


def contains(text: str, part: str) -> bool:
    """``part`` (normalised) is whole words of ``text`` (normalised)."""
    return bool(part) and f" {part} " in f" {text} "


def too_short(quote: Any) -> bool:
    """Fewer than ``MIN_VERIFY_WORDS`` words: too generic to prove a location by."""
    return len(normalize(quote).split()) < MIN_VERIFY_WORDS


def _adjacent_joins(index: Index) -> Index:
    """Each pair of consecutive paragraphs as one text: a quote may run across the boundary."""
    ordered = sorted(index)
    return [
        (a, f"{ta} {tb}")
        for (a, ta), (b, tb) in zip(ordered, ordered[1:])
        if b == a + 1
    ]


def fragments(quote: Any) -> List[str]:
    """A quote's normalised parts: one, or one per side of each elision ("A ... B" quotes two
    passages of the source, never the literal "A ... B")."""
    parts = [normalize(p) for p in _ELISION.split(str(quote or ""))]
    return [p for p in parts if p]


def _find_part(part: str, index: Index, fuzzy: bool) -> Tuple[Optional[int], str]:
    """One part of a quote: exact in a paragraph, else exact across two consecutive ones, else
    (``fuzzy``) near."""
    num, how = find_quote(part, index, fuzzy=False)
    if not how:
        num, how = find_quote(part, _adjacent_joins(index), fuzzy=False)
    if not how and fuzzy:
        num, how = find_quote(part, index, fuzzy=True)
    return num, how


def in_paragraphs(
    quote: Any, index: Index, numbers: Optional[Iterable[int]] = None
) -> Tuple[Optional[int], str]:
    """Where ``quote`` is, limited to the paragraphs ``numbers`` (all when None): the paragraph of
    its first part; ``EXACT`` when every part is exact (within one paragraph, or across two
    consecutive ones), ``NEAR`` when every part is at least near.

    A near match is looked for only within ``numbers``: across a whole chapter it is neither
    cheap nor meaningful."""
    if numbers is not None:
        wanted = set(numbers)
        index = [(n, t) for n, t in index if n in wanted]
    found = [_find_part(p, index, numbers is not None) for p in fragments(quote)]
    if not found or any(not how for _, how in found):
        return None, ""
    return found[0][0], EXACT if all(h == EXACT for _, h in found) else NEAR
