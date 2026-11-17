"""Find a quoted passage in source paragraphs - the ONE matcher for "is this quote in the book?".

Used to resolve an anchor quote to its paragraph (``src/extraction/anchor_resolve``) and to verify a
record's quoted provenance (``scripts/validate_provenance``). A quote either appears in the source
text or it does not: an exact match (after normalising both sides) proves it; a near match (OCR
noise, a model's small rewording) is reported as such, never as proof.

Normalising strips what the parsed markdown carries and a quote does not: footnote markers
(``<sup>[1](url)</sup>``, and the bare ``[1]`` a model copies of one), images, link targets,
emphasis (``_Seventh Army,_``) and page markers (``_\\--212--_``); then case, punctuation and
whitespace.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

# The ONE quote rule of every extractor's prompt (Python prompts import it; YAML prompts get it as
# ``{quote_rule}`` from prompt_loader.render_prompt). original_text is the provenance anchor and
# how a paragraph window's item finds its real sub-event (windows.attribute): it must be IN the
# text. Asked for "the sentence(s)" (with an example ending in "..."), people quotes spliced
# sentences together with ellipses - ~70% verbatim against 97-100% for the other entities
# (chapter 8 window test, 2026-11-13).
QUOTE_RULE = (
    "ONE passage copied exactly from the text, word for word (the sentence that names it) - "
    "never join separate sentences, never shorten with '...', never paraphrase"
)

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
# a note marker without its <sup>: "the war.[3]" - a number only, never an editorial "[Krancke ...]"
_BARE_NOTE = re.compile(r"\[\d{1,3}[a-z]?\]")
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
    t = _BARE_NOTE.sub(" ", t)
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


# A quote whose first part is NOT at the end of its paragraph (the parser set a note's text after
# it) bridges into the next one only with this many words on each side: a one-word "head" would
# let a spliced "Marshall | Patton said he would go" pass as verbatim
_MIN_BRIDGE_HEAD, _MIN_BRIDGE_TAIL = 6, 4


def _across(part: str, index: Index) -> Optional[int]:
    """``a`` when ``part`` runs from paragraph ``a`` into the START of paragraph ``a + 1``: from
    the END of ``a`` (a page break splits a sentence: "... given the shadow. The" | "Combined
    Chiefs of Staff ..."), or - with ``_MIN_BRIDGE_HEAD`` / ``_MIN_BRIDGE_TAIL`` words either
    side - from inside it (the parser set a footnote's text between a sentence's halves:
    "dropped the majority" + note text | "of the 2d Battalion outside their assigned zone").
    """
    words = part.split()
    texts = dict(index)
    for a, ta in sorted(index):
        tb = texts.get(a + 1)
        if tb and any(_bridges(words, k, ta, tb) for k in range(1, len(words))):
            return a
    return None


def _bridges(words: List[str], k: int, ta: str, tb: str) -> bool:
    """``words[:k]`` ends paragraph ``ta`` (or is long enough and inside it) and ``words[k:]``
    starts ``tb``."""
    head, tail = " ".join(words[:k]), " ".join(words[k:])
    if not f"{tb} ".startswith(f"{tail} "):
        return False
    if f" {ta}".endswith(f" {head}"):
        return True
    # inside the paragraph: two unrelated runs of 6 and 4 words lining up by chance is unlikely
    long_enough = k >= _MIN_BRIDGE_HEAD and len(words) - k >= _MIN_BRIDGE_TAIL
    return long_enough and contains(ta, head)


def fragments(quote: Any) -> List[str]:
    """A quote's normalised parts: one, or one per side of each elision ("A ... B" quotes two
    passages of the source, never the literal "A ... B")."""
    parts = [normalize(p) for p in _ELISION.split(str(quote or ""))]
    return [p for p in parts if p]


def _find_part(part: str, index: Index, fuzzy: bool) -> Tuple[List[int], str]:
    """One part of a quote: (its paragraphs, how) - exact in a paragraph, else exact across two
    consecutive ones (``_across``: both paragraphs), else (``fuzzy``) near."""
    num, how = find_quote(part, index, fuzzy=False)
    if how:
        return [num], how  # type: ignore[list-item]
    a = _across(part, index)
    if a is not None:
        return [a, a + 1], EXACT
    num, how = find_quote(part, index, fuzzy=True) if fuzzy else (None, "")
    return ([num] if how else []), how  # type: ignore[list-item]


def locate(
    quote: Any, index: Index, numbers: Optional[Iterable[int]] = None
) -> Tuple[List[int], str]:
    """(every paragraph ``quote`` is in, in order, ``EXACT`` | ``NEAR`` | "") within the paragraphs
    ``numbers`` (all when None): ``EXACT`` when every part is exact (within one paragraph, or
    across two consecutive ones), ``NEAR`` when every part is at least near.

    A near match is looked for only within ``numbers``: across a whole chapter it is neither
    cheap nor meaningful."""
    scoped = _within(index, numbers)
    found = [_find_part(p, scoped, numbers is not None) for p in fragments(quote)]
    if not found or not all(how for _, how in found):
        return [], ""
    nums = sorted({n for ns, _ in found for n in ns})
    return nums, EXACT if all(h == EXACT for _, h in found) else NEAR


def _within(index: Index, numbers: Optional[Iterable[int]]) -> Index:
    if numbers is None:
        return index
    wanted = set(numbers)
    return [(n, t) for n, t in index if n in wanted]


def in_paragraphs(
    quote: Any, index: Index, numbers: Optional[Iterable[int]] = None
) -> Tuple[Optional[int], str]:
    """Where ``quote`` is (``locate``): its first paragraph, or None."""
    nums, how = locate(quote, index, numbers)
    return (nums[0] if nums else None), how


# --- the source's own words for a quote that is not verbatim -----------------------------------
_EMPHASIS = re.compile(r"(?<!\w)[*_]+|[*_]+(?!\w)")
# a sentence end ...
_PIECE_END = re.compile(r"(?<=[.!?;:])\s+")
# ... unless after an abbreviation or initial ("Lt. Gen. Omar N. Bradley", "U.S.", "St. Lo") -
# a fixed list: "War." / "so." end sentences
_ABBREVIATIONS = (
    "Lt|Lieut|Gen|Gens|Col|Cols|Capt|Brig|Maj|Sgt|Cpl|Pvt|Pfc|Adm|Comdr|Cdr|Cmdr|Rear|Vice|"
    "St|Ste|Mt|Ft|Dr|Mr|Mrs|Messrs|Jr|Sr|Co|Cos|Bn|Regt|Div|Inf|Bde|No|Nos|Vol|Vols|Ch|Chap|"
    "p|pp|n|nn|ff|cf|op|cit|loc|ibid|Ibid|viz|vs|etc|ca|approx|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|"
    "Sept|Oct|Nov|Dec"
)
_ABBREVIATION = re.compile(
    rf"(?:^|[\s(\[\"'])(?:{_ABBREVIATIONS}|[A-Z]|(?:[A-Za-z]\.)+[A-Za-z])\.$"
)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?)\]])")
# the most sentence pieces one passage may span
_MAX_PIECES = 4


def plain(text: Any) -> str:
    """Source text as a reader sees it: note markers, images, link targets, page markers, tags
    and emphasis removed, case and punctuation KEPT, whitespace collapsed. Its ``normalize`` is
    whole words of the source paragraph's."""
    t = unicodedata.normalize("NFKC", _unmangle(str(text or "")))
    for pattern in (_SUP, _IMAGE):
        t = pattern.sub(" ", t)
    t = _LINK.sub(r"\1", t)
    for pattern in (_BARE_NOTE, _PAGE_MARK, _TAG, _EMPHASIS):
        t = pattern.sub(" ", t)
    return _SPACE_BEFORE_PUNCT.sub(r"\1", " ".join(t.split()))


def _starts(piece: str) -> bool:
    """A piece can start a sentence: a capital, a digit, or an opening quote / bracket."""
    first = piece[:1]
    return first.isupper() or first.isdigit() or first in "\"'\u201c\u2018(["


def _pieces(text: Any) -> List[str]:
    """The source's sentences (``plain``): split at a sentence end, rejoined after an
    abbreviation or initial, or before a piece that does not start a sentence (lower case).
    """
    pieces: List[str] = []
    for piece in _PIECE_END.split(plain(text)):
        if pieces and (_ABBREVIATION.search(pieces[-1]) or not _starts(piece)):
            pieces[-1] = f"{pieces[-1]} {piece}"
        elif piece:
            pieces.append(piece)
    return pieces


def source_passage(quote: Any, text: Any) -> Optional[str]:
    """The shortest run of the source ``text``'s sentences that holds ``quote``'s words (at least
    ``FUZZY_THRESHOLD`` of them, in order), verbatim (``plain``) - what a near-matched quote is
    replaced by. None when no run does."""
    norm_quote = " ".join(normalize(p) for p in fragments(quote))
    if len(norm_quote.split()) < MIN_FUZZY_WORDS:
        return None  # a 2-3 word quote "nearly matches" half the book
    pieces = _pieces(text)
    best: Tuple[float, int, str] = (0.0, 0, "")
    for i in range(len(pieces)):
        for j in range(i + 1, min(i + _MAX_PIECES, len(pieces)) + 1):
            run = " ".join(pieces[i:j])
            score = _word_ratio(norm_quote, normalize(run))
            if (score, -len(run)) > (best[0], best[1]):
                best = (score, -len(run), run)
    return best[2] if best[0] >= FUZZY_THRESHOLD else None


def naming_sentence(name: Any, text: Any) -> Optional[str]:
    """The first sentence of the source ``text`` that names ``name`` (whole words, or its plural:
    "LCT" in "The LCTs beached"), verbatim (``plain``), or None. A name an abbreviation's full
    stop split ("Lt. | Gen. Bradley") is found in the piece and the next together."""
    norm_name = normalize(name)
    forms = (norm_name, f"{norm_name}s", f"{norm_name}es") if norm_name else ()
    pieces = _pieces(text) if forms else []
    runs = pieces + [f"{a} {b}" for a, b in zip(pieces, pieces[1:])]
    return next(
        (r for r in runs if any(contains(normalize(r), f) for f in forms)), None
    )
