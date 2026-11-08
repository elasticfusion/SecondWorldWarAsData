"""Cross-check an AUTHORITATIVE scanned-PDF text layer against a HyperWar HTML transcription.

Owner rule (2026-11-08): the PDF (U.S. Army Center of Military History edition) is authoritative;
the HyperWar transcription is a convenience copy. Neither is error-free: the PDF's 2010 OCR layer
reads "101st" as "lOlst" and "Army" as "A rmy"; the transcription has "Spieled" for Speidel and
"Stag" for Stagg. This module aligns the two word streams deterministically (no model) and sorts
every disagreement into:

* ``ocr_spacing``   - same letters, different word breaks, and the PDF's is not the book's
  established form ("A rmy" / "Army"; "counter-attack" - a line-break hyphen kept, where the book
  prints "counterattack" 82 times and never hyphenates it mid-line): correctable.
* ``book_form``     - same letters, and the PDF's form is the book's own ("co-ordinated", printed
  so 28 times mid-line; "1,240"): the transcription normalised it; the PDF is right.
* ``ocr_homoglyph`` - Latin letters OCR'd as Cyrillic look-alikes ("УТАН" for UTAH): correctable.
* ``ocr_confusion`` - equal after the classic OCR confusions (l/1/I, O/0, rn/m, vv/w, W/V,
  cl/d, S/5, B/8), and the PDF reading occurs nowhere else in the book ("lOlst"; "fail" beside
  "fall" is a real difference): PDF artefact, correctable. Never for a number or Roman numeral
  on EITHER side; a frequency or second-OCR verdict on the pair takes precedence.
* ``ocr_frequency`` - the PDF reading occurs at most twice in the WHOLE book while the HTML
  reading is established in it (>= 3 times, >= 10x as often): "Dallmann" once vs "Dollmann"
  throughout - an OCR error the book itself corrects.
* ``ocr_crossread`` - a second OCR of the SAME page images (the PDF's 2010 text layer, when the
  PDF reading is Chandra's) has the transcription's word on these pages and never the PDF
  reading's ("Schwarzwalder" vs Chandra's "Schwarzwaldor"): correctable. Never for a word with
  a digit, a Roman numeral or an accent (the 2010 layer has none). The reverse - both OCRs read
  the PDF's word, the second never the transcription's - is ``html_error``; evidence that
  disagrees is ``review``.
* ``html_error``    - the reverse: the PDF reading is established in the book and the HTML one
  is not ("Speidel" vs "Spieled", "killed" vs "kilied" - a look-alike on the transcription's
  side): the transcription is wrong, or follows the older 1951 printing where the PDF is the
  newer (1993) one - which is deferred to, above all for people's names. The PDF needs nothing.
* ``html_accent``   - the transcription dropped an accent the book prints, and prints more than
  once ("Periers" for Périers): the PDF is right.
* ``moved``         - the same words on both sides but in the other stream (a note continued on
  the next page arrives without its number and reads as narrative): not a difference.
* ``layout``        - running heads, page numbers, photo captions in the PDF stream: not text.
* ``review``        - a real difference neither rule settles (a number, a Roman numeral, two valid
  words): the PDF page must be looked at. Each item carries the PDF page, the printed page and
  the line's position, so a crop of the authoritative image can be checked in seconds.
* ``pdf_only`` / ``html_only`` - text present on one side only: review.

Every rule but the homoglyph one needs the book's vocabulary as evidence (``pdf_vocabulary``);
without it a difference is ``review``.

``decided_share`` (correctable OCR + transcription errors, over all real differences) answers
whether "PDF text with its OCR artefacts corrected" can be the source of record: the PDF stays
authoritative and the HTML only helps find and explain its artefacts.
"""

from __future__ import annotations

import difflib
import html as htmlmod
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

CORRECTABLE = (
    "ocr_spacing", "ocr_homoglyph", "ocr_confusion", "ocr_frequency", "ocr_crossread",
)  # fmt: skip
PDF_RIGHT = ("html_error", "html_accent", "book_form")
NOT_TEXT = ("layout", "moved")
NEEDS_A_LOOK = ("review", "pdf_only", "html_only")
# Letters of ANY script: Chandra writes small caps as Cyrillic look-alikes ("УТАН" for UTAH),
# which an ASCII-only pattern would silently drop from the comparison.
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?")
_ROMAN = re.compile(r"^[IVXLC]+$")
_DIGITS = re.compile(r"^\d+$")
# Cyrillic / Greek letters that look like Latin ones (OCR of small capitals: "УТАН", "ОМАНА").
_HOMOGLYPHS = str.maketrans(
    "аеорсухтнмкві" "αεορτυικνχ",
    "aeopcyxthmkbi" "aeoptuikvx",
)
_HOMOGLYPH_U = str.maketrans({"у": "u"})  # small-caps "U" read as Cyrillic "У"
# Classic OCR confusions, applied in this order to BOTH readings (lower-cased) before comparing
# ("vv" -> "w" before "w" -> "v", so "vv", "w" and "v" all end as "v").
_CONFUSIONS = (
    ("rn", "m"), ("vv", "w"), ("cl", "d"), ("w", "v"),
    ("1", "l"), ("i", "l"), ("0", "o"), ("5", "s"), ("8", "b"),
)  # fmt: skip
_HOMOGLYPH_CHARS = "аеорсухтнмкві" "αεορτυικνχ"


@dataclass
class Token:
    text: str
    page: Optional[int] = None  # PDF page number (1-based) for PDF tokens
    printed: Optional[int] = None  # printed page number
    bbox: Optional[Tuple[float, ...]] = None
    hyphen: bool = (
        False  # the PDF line ended "word-": joined to the next line's first word
    )
    kind: str = "body"  # body | note | layout
    joined: str = ""  # "-" or "," joining this word to the previous one in the source
    # (start, end) of the word in the source markdown (Chandra), when it is one contiguous run
    span: Optional[Tuple[int, int]] = None


def _normalised(text: str) -> str:
    """Entities unescaped, NFC (a decomposed "é" is one letter, not "e" + a mark that splits the
    word) and the typographic apostrophe as "'" ("Johnson’s" is one word)."""
    return unicodedata.normalize("NFC", htmlmod.unescape(text or "")).replace("’", "'")


def words(text: str) -> List[str]:
    return _WORD.findall(_normalised(text))


_COMPOUND = re.compile(r"[^\W_]+(?:-[^\W_]+)+")


def joined_words(text: str) -> List[Tuple[str, str]]:
    """(word, joiner) pairs: the joiner is "-" for a hyphenated compound ("co-ordinated") and ","
    for digit grouping ("1,240"), else "". A hyphen followed by a space is NOT joined: it is a
    German suspended compound ("Atlantik- und Kanalkueste") as often as a kept line break.
    """
    return [(w, j) for w, j, _span in _joined_spans(text)]


def _joined_spans(text: str) -> List[Tuple[str, str, Optional[Tuple[int, int]]]]:
    """``joined_words`` with each word's (start, end) in ``text`` - None when normalising
    changed the text's length (an entity, a decomposed accent), so offsets would not hold.
    """
    normal = _normalised(text)
    exact = len(normal) == len(text)
    out: List[Tuple[str, str, Optional[Tuple[int, int]]]] = []
    end: Optional[int] = None
    for m in _WORD.finditer(normal):
        word, gap = m.group(0), normal[end : m.start()] if end is not None else ""
        end = m.end()
        digits = gap == "," and out and out[-1][0].isdigit() and word.isdigit()
        joiner = gap if gap == "-" or digits else ""
        out.append((word, joiner, (m.start(), m.end()) if exact else None))
    return out


# --- PDF side ----------------------------------------------------------------------------------------


@dataclass
class PdfLayout:
    """Font-size bands of a scanned book's OCR layer (CMH green books: body ~10.4pt, notes
    7.5-8.6pt, running head at the top of the page)."""

    note_max_size: float = 9.2
    head_band: float = (
        0.11  # top fraction of the page holding the running head / page number
    )


def _line_text(line: Dict[str, Any]) -> str:
    return "".join(s.get("text", "") for s in line.get("spans", []))


def _line_size(line: Dict[str, Any]) -> float:
    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
    return sum(s["size"] for s in spans) / len(spans) if spans else 0.0


def _printed_page(
    lines: List[Dict[str, Any]], height: float, layout: PdfLayout
) -> Optional[int]:
    """The page number printed in the running head (a digits-only line at the top)."""
    for line in lines:
        text = _line_text(line).strip()
        if line["bbox"][1] < height * layout.head_band and _DIGITS.match(text):
            return int(text)
    return None


def _line_kind(line: Dict[str, Any], height: float, layout: PdfLayout) -> str:
    if (
        line["bbox"][1] < height * layout.head_band
        and len(words(_line_text(line))) <= 8
    ):
        return "layout"  # running head / page number
    return "note" if _line_size(line) <= layout.note_max_size else "body"


def _reading_order(lines, rect, layout: PdfLayout) -> List[Tuple[str, Dict[str, Any]]]:
    """(kind, line) in reading order: the running head, then the body column by column, then the
    notes column by column. The OCR layer's own order interleaves the two columns on some pages
    ("While Fif- put its troops ...")."""
    kinds = [(_line_kind(ln, rect.height, layout), ln) for ln in lines]
    mid = rect.width / 2

    def order(item):
        x0, y0, x1, _y1 = item[1]["bbox"]
        return (0 if (x0 + x1) / 2 < mid else 1, y0)

    out = [kl for kl in kinds if kl[0] == "layout"]
    for kind in ("body", "note"):
        out += sorted((kl for kl in kinds if kl[0] == kind), key=order)
    return out


def _page_lines(page: Any) -> List[Dict[str, Any]]:
    """The non-empty text lines of a PDF page's OCR layer."""
    return [
        ln
        for b in page.get_text("dict")["blocks"]
        if b.get("type") == 0
        for ln in b["lines"]
        if _line_text(ln).strip()
    ]


def pdf_tokens(
    doc: Any, first: int, last: int, layout: Optional[PdfLayout] = None
) -> List[Token]:
    """Word tokens of PDF pages ``first..last`` (1-based, inclusive) in reading order, each with
    its page, printed page, line bbox and kind (body / note / layout)."""
    layout = layout or PdfLayout()
    out: List[Token] = []
    for number in range(first, last + 1):
        page = doc[number - 1]
        lines = _page_lines(page)
        printed = _printed_page(lines, page.rect.height, layout)
        for kind, line in _reading_order(lines, page.rect, layout):
            _add_line_tokens(out, line, number, printed, kind)
    return out


def _add_line_tokens(
    out: List[Token], line: Dict[str, Any], page: int, printed: Optional[int], kind: str
) -> None:
    _add_tokens(out, _line_text(line), page, printed, kind, tuple(line["bbox"]))


def _add_tokens(
    out: List[Token],
    text: str,
    page: int,
    printed: Optional[int],
    kind: str,
    bbox: Optional[Tuple[float, ...]] = None,
    base: Optional[int] = None,
) -> None:
    """Append ``text``'s words to ``out``; a word the previous line of the same stream broke
    with a hyphen ("fortifica-" / "tions") is joined, so a caption or note printed between the
    halves does not split it. A word broken over three lines ("in-" / "dica-" / "ted") joins
    whole. Limit: a genuine compound broken at its hyphen ("co-" / "ordinated") is joined too;
    the book's vocabulary then judges the joined form (``_by_form``). ``base``: the offset of
    ``text`` in the source markdown, for each token's ``span`` (a joined word has none).
    """
    tokens = _joined_spans(text)
    ends_hyphen = _ends_with_break(text)
    prev = next((t for t in reversed(out) if t.kind == kind), None)
    if prev is not None and prev.hyphen and tokens:
        prev.text += tokens.pop(0)[0]
        prev.hyphen = ends_hyphen and not tokens  # "dica-" consumed whole: still broken
        prev.span = None  # two runs of the source: not one place to correct
    for w, joiner, span in tokens:
        at = _offset(span, base)
        out.append(Token(w, page, printed, bbox, kind=kind, joined=joiner, span=at))
    if tokens and ends_hyphen:
        out[-1].hyphen = True


def _offset(
    span: Optional[Tuple[int, int]], base: Optional[int]
) -> Optional[Tuple[int, int]]:
    if base is None or span is None:
        return None
    return (base + span[0], base + span[1])


def _ends_with_break(text: str) -> bool:
    """The text ends "word-" (a broken word), not "--" (a dash)."""
    stripped = text.rstrip()
    return stripped.endswith("-") and not stripped.endswith("--")


def pdf_vocabulary(doc: Any, skip: Iterable[int] = ()) -> Counter:
    """How often each (lower-cased) word, and each hyphenated compound printed within a line
    ("co-ordinated"), occurs in the WHOLE PDF text layer: the book's own evidence for which
    reading or form of a disputed word is established. ``skip``: PDF page numbers (1-based) to
    leave out - the chapter under check when its words are counted from another reading
    (Chandra), so no page counts twice."""
    skipped = set(skip)
    vocab: Counter = Counter()
    for number, page in enumerate(doc, start=1):
        if number in skipped:
            continue
        text = _normalised(page.get_text())
        vocab.update(w.lower() for w in _WORD.findall(text))
        vocab.update(c.lower() for c in _COMPOUND.findall(text))
    return vocab


def token_vocabulary(tokens: Sequence[Token]) -> Counter:
    """``pdf_vocabulary`` of a token stream: its words and its hyphenated compounds."""
    vocab: Counter = Counter(t.text.lower() for t in tokens)
    run: List[str] = []
    for t in list(tokens) + [Token("")]:
        if t.joined == "-" and run:
            run.append(t.text)
            continue
        if len(run) > 1:
            vocab["-".join(run).lower()] += 1
        run = [t.text]
    return vocab


# --- Chandra OCR markdown (what Phase 0 produces for a scanned PDF) -------------------------------------

_NOTE_DEF = re.compile(r"\s*<sup>\s*\d{1,4}\s*</sup>")
# A photo caption: an ALL-CAPS title, then its italic description ("**PARACHUTE TROOPS**
# *marching onto ...*", "NINTH AIR FORCE B-26 OVER BRITISH BEACHHEAD at *Lion-sur-Mer*. *Note ...*").
_ITALIC = re.compile(r"(?<!\*)\*[^*\s][^*]*\*(?!\*)")
_MD_MARKUP = re.compile(r"<[^>]+>|[*_#>|`]")
_SHINGLE = 5  # words in a placement shingle
_PLACEMENT_LEAD = 12  # opening words of a paragraph probed for placement


def chandra_tokens(
    markdown: str,
    first_page: int,
    printed: Optional[Dict[int, int]] = None,
    streams: Optional[Tuple[Sequence[Token], Sequence[Token]]] = None,
    stats: Optional[Counter] = None,
) -> List[Token]:
    """Word tokens of Chandra's paginated markdown (``src.ingestion.chunk_pages`` separators),
    each with its PDF page and printed page (from ``printed``, e.g. the PDF's own running-head
    numbers). Judged PARAGRAPH by paragraph (blank-line separated):

    * a paragraph opening with ``<sup>N</sup>`` is a footnote definition (Chandra writes each note
      as its own paragraph, and body text may follow it on the same page);
    * a paragraph opening with ``![...](...)`` is an image: its alt text (Chandra's description
      of the picture) is MODEL-WRITTEN, not the book's text, and is dropped;
    * an ALL-CAPS title followed by an italic description is a photo caption (layout);
    * anything else is body text - unless ``streams`` (the transcription's (narrative, notes))
      places it: a paragraph whose opening words occur in the notes and not in the narrative is
      a note continued from the previous page (printed without its number, and placed by Chandra
      anywhere on the page). Only the paragraph's STREAM is taken from the transcription, never
      its text.

    A word hyphenated across a line, paragraph or page break ("in-" / "dicated") is joined within
    its own stream, so a caption or note printed mid-word does not split it. ``stats`` (if
    given) counts the paragraphs of each judgement."""
    placed = _note_placer(streams)
    stats = stats if stats is not None else Counter()
    out: List[Token] = []
    for page, start, stripped in _chandra_paragraphs(markdown, first_page):
        if stripped.startswith("!["):
            stats["image_dropped"] += 1
            continue
        # markup blanked to spaces of its own length, so word offsets stay true to the source
        body = _MD_MARKUP.sub(lambda m: " " * len(m.group(0)), stripped)
        kind = _chandra_kind(stripped, body, placed)
        stats[kind] += 1
        printed_page = (printed or {}).get(page)
        _add_tokens(out, body, page, printed_page, kind.split(":")[0], base=start)
    return out


def _chandra_paragraphs(
    markdown: str, first_page: int
) -> Iterable[Tuple[int, int, str]]:
    """(PDF page, offset in ``markdown``, paragraph) of every non-blank paragraph, paginated as
    ``src.ingestion.chunk_pages.split_pages`` does (pages numbered on from ``first_page``).
    """
    from src.ingestion.chunk_pages import PAGE_SEPARATOR_RE

    bounds, pos = [], 0
    for m in PAGE_SEPARATOR_RE.finditer(markdown):
        bounds.append((pos, m.start()))
        pos = m.end()
    bounds.append((pos, len(markdown)))
    for page, (start, end) in enumerate(bounds, start=first_page):
        piece_start = start
        for sep in list(_PARAGRAPH_BREAK.finditer(markdown, start, end)) + [None]:
            piece_end = sep.start() if sep else end
            piece = markdown[piece_start:piece_end]
            stripped = piece.strip()
            if stripped:
                yield page, piece_start + len(piece) - len(piece.lstrip()), stripped
            piece_start = sep.end() if sep else end


_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _chandra_kind(paragraph: str, body: str, placed) -> str:
    """body | note | note:placed | layout"""
    if _NOTE_DEF.match(paragraph):
        return "note"
    if _is_chandra_caption(paragraph):
        return "layout"
    return "note:placed" if placed(body) else "body"


def _is_chandra_caption(paragraph: str) -> bool:
    """The words before the first italic are an ALL-CAPS title (at most one lower-case word, as
    in "... BEACHHEAD at *Lion-sur-Mer*"): "XIX CORPS ordered the attack on *Texas*" is not.
    """
    italic = _ITALIC.search(paragraph)
    if not italic:
        return False
    title = words(_MD_MARKUP.sub(" ", paragraph[: italic.start()]))
    lower = sum(1 for w in title if not (w.isupper() or w.isdigit()))
    return _is_caption_like([Token(w) for w in title], lead=2) and lower <= 1


def _note_placer(streams: Optional[Tuple[Sequence[Token], Sequence[Token]]]):
    """``placed(text)``: do the opening words of ``text`` occur in the notes stream only? Two
    5-word shingles of the first 12 words must be found in the notes and none in the narrative,
    so a transcription typo in the opening ("conducted dunng July") does not defeat it.
    """
    if not streams:
        return lambda _text: False
    narrative, notes = (
        " " + " ".join(t.text.lower() for t in s) + " " for s in streams
    )

    def placed(text: str) -> bool:
        lead = [w.lower() for w in words(text)[:_PLACEMENT_LEAD]]
        shingles = [
            " " + " ".join(lead[i : i + _SHINGLE]) + " "
            for i in range(len(lead) - _SHINGLE + 1)
        ]
        if any(s in narrative for s in shingles):
            return False
        return sum(1 for s in shingles if s in notes) >= 2

    return placed


def printed_pages(doc: Any, first: int, last: int) -> Dict[int, int]:
    """{PDF page: printed page} from the running-head page numbers of the PDF's text layer."""
    layout = PdfLayout()
    out: Dict[int, int] = {}
    for number in range(first, last + 1):
        page = doc[number - 1]
        found = _printed_page(_page_lines(page), page.rect.height, layout)
        if found is not None:
            out[number] = found
    return out


# --- HTML side ---------------------------------------------------------------------------------------


_PAGE_ANCHOR = re.compile(r'<a id="page(\d+)"></a>')


def html_streams(normalised_html: str) -> Tuple[List[Token], List[Token]]:
    """(narrative tokens, note tokens) of a normalised HyperWar page
    (src/ingestion/hyperwar_html.normalise_hyperwar_html). Figures and captions are left out (the
    PDF prints them apart from the text); page anchors give each token its printed page. The
    transcriber's ``<address>`` credit is not the book's text.
    """
    normalised_html = re.sub(
        r"<address\b.*?</address>", " ", normalised_html, flags=re.S | re.I
    )
    parts = _NOTES_HEADING.split(normalised_html, maxsplit=1)
    body, notes = (parts[0], parts[1]) if len(parts) == 2 else (normalised_html, "")
    return _html_tokens(_narrative(body)), _html_tokens(notes)


# The notes heading, not any mention of the word in the prose.
_NOTES_HEADING = re.compile(r"<h[1-6]\b[^>]*>\s*Footnotes\s*</h[1-6]>", re.I)


def _narrative(body: str) -> str:
    """Paragraphs, quotations, lists and headings, without figures (images/tables/captions)."""
    body = re.sub(r"<center\b.*?</center>", _keep_headings, body, flags=re.S | re.I)
    body = re.sub(r"<table\b.*?</table>", " ", body, flags=re.S | re.I)
    return re.sub(r"<div>.*?</div>", " ", body, flags=re.S | re.I)


def _keep_headings(m: "re.Match[str]") -> str:
    return " ".join(re.findall(r"<h[1-6]\b[^>]*>.*?</h[1-6]>", m.group(0), re.S | re.I))


def _html_tokens(html: str) -> List[Token]:
    out: List[Token] = []
    page: Optional[int] = None
    pos = 0
    for m in _PAGE_ANCHOR.finditer(html):
        out.extend(
            Token(w, printed=page) for w in words(_strip_tags(html[pos : m.start()]))
        )
        page = int(m.group(1))
        pos = m.end()
    out.extend(Token(w, printed=page) for w in words(_strip_tags(html[pos:])))
    return out


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


# --- classification ----------------------------------------------------------------------------------


def _unaccented(text: str) -> str:
    """Accents removed; NFD, not NFKD, so a ligature ("ﬁ") is not mistaken for an accent."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)
    )


def has_homoglyphs(word: str) -> bool:
    return any(ch.lower() in _HOMOGLYPH_CHARS for ch in word)


def _confused(word: str) -> str:
    w = word.lower().translate(_HOMOGLYPH_U).translate(_HOMOGLYPHS)
    for a, b in _CONFUSIONS:
        w = w.replace(a, b)
    return w


def _is_caption_like(tokens: Sequence[Token], lead: int = 3) -> bool:
    """A photo caption starts with an ALL-CAPS title: "CROSSING THE CHANNEL. Columns of ...".
    Three words by default: two ("XIX CORPS ordered ...", "FUSA reported ...") open real
    sentences, which must stay in review."""
    head = [t.text for t in tokens[:lead]]
    return len(head) == lead and all(
        len(w) > 1 and w.isalpha() and w.isupper() for w in head
    )


def _is_numeric_claim(tokens: Sequence[Token]) -> bool:
    """Numbers and Roman numerals are claims (units, dates, corps): never auto-decided when the
    readings differ as NUMBERS (VIII vs VII); a letter/digit look-alike inside a unit designation
    ("101st" / "lOlst") is still OCR."""
    return any(_DIGITS.match(t.text) or _ROMAN.match(t.text) for t in tokens)


def classify(
    html_part: Sequence[Token],
    pdf_part: Sequence[Token],
    vocab: Optional[Counter] = None,
    second: Optional[Counter] = None,
) -> str:
    """The class of one aligned difference (see the module docstring). ``vocab``: the whole
    book's words (``pdf_vocabulary``); ``second``: another OCR's words of the same pages.
    """
    if not pdf_part or not html_part:
        return _one_sided_class(pdf_part)
    a = "".join(t.text for t in html_part).lower()
    b = "".join(t.text for t in pdf_part).lower()
    if a == b:
        return _by_form(html_part, pdf_part, vocab)
    if has_homoglyphs(b) and a == b.translate(_HOMOGLYPH_U).translate(_HOMOGLYPHS):
        return "ocr_homoglyph"
    if not vocab:
        return "review"
    return _by_reading(a, b, (html_part, pdf_part), vocab, second)


def _one_sided_class(pdf_part: Sequence[Token]) -> str:
    """Text on one side only: ``html_only`` (no PDF text), ``layout`` or ``pdf_only``."""
    if not pdf_part:
        return "html_only"
    if all(t.kind == "layout" for t in pdf_part) or _is_caption_like(pdf_part):
        return "layout"
    return "pdf_only"


def _by_reading(
    a: str,
    b: str,
    parts: Tuple[Sequence[Token], Sequence[Token]],
    vocab: Counter,
    second: Optional[Counter],
) -> str:
    """Different letters: accents, look-alike letters, then the evidence on single words."""
    if _unaccented(a) == _unaccented(b):
        # Only the accents differ. The transcription dropping one the book prints, and prints
        # again ("Periers" / "Périers"), is settled; an accent once only may be the OCR's own.
        settled = _unaccented(b) == a and vocab[b] >= 2
        return "html_accent" if settled else "review"
    verdict = _word_verdict(parts, vocab, second)
    if _confused(a) == _confused(b) and not any(map(_is_numeric_claim, parts)):
        return _confusion_verdict(verdict, b, vocab)
    return verdict or "review"


def _word_verdict(
    parts: Tuple[Sequence[Token], Sequence[Token]],
    vocab: Counter,
    second: Optional[Counter],
) -> Optional[str]:
    """The book's frequencies first, then the second OCR; if they disagree, a human looks."""
    verdict = _by_frequency(parts[0], parts[1], vocab)
    cross = _by_second_reading(parts[0], parts[1], second) if second else None
    if verdict and cross and _blamed(verdict) != _blamed(cross):
        return "review"
    return verdict or cross


def _blamed(cls: str) -> str:
    """Which side a verdict says is wrong: the transcription, or the PDF's OCR."""
    return "html" if cls in PDF_RIGHT else "pdf"


def _confusion_verdict(verdict: Optional[str], pdf_reading: str, vocab: Counter) -> str:
    """Look-alike letters. A verdict from the evidence stands as is ("kilied" is the
    transcription's error, a conflict is ``review``). Without one, the look-alike is the OCR's
    only when the PDF reading occurs nowhere else in the book (it counts itself once):
    "lOlst" is no word, "fail" beside "fall" is a real difference."""
    if verdict:
        return verdict
    return "ocr_confusion" if vocab[pdf_reading] <= 1 else "review"


def _by_form(
    html_part: Sequence[Token], pdf_part: Sequence[Token], vocab: Optional[Counter]
) -> str:
    """Same letters, different breaks: the form the WHOLE book establishes decides whether the
    PDF's form is the book's own (``book_form``) or an OCR break (``ocr_spacing``)."""
    joiners = {t.joined for t in pdf_part[1:]}
    if joiners == {","}:
        return "book_form"  # "1,240": the book's digit grouping
    if not vocab:
        return "review"  # no evidence from the book: look at the page
    book = _established_form(pdf_part if len(pdf_part) > 1 else html_part, vocab)
    pdf_form = _printed_form(pdf_part, joiners)
    if book is None or _both_forms_valid(pdf_part, pdf_form, vocab):
        return "review"
    return "book_form" if pdf_form == book else "ocr_spacing"


def _both_forms_valid(pdf_part: Sequence[Token], pdf_form: str, vocab: Counter) -> bool:
    """ "per cent" / "percent": the PDF's pieces are established words of their own."""
    return pdf_form == "space" and all(vocab[t.text.lower()] >= 3 for t in pdf_part)


def _printed_form(pdf_part: Sequence[Token], joiners: set) -> str:
    if len(pdf_part) == 1:
        return "joined"
    return "hyphen" if joiners == {"-"} else "space"


def established_form(pieces: Sequence[str], vocab: Counter) -> Optional[str]:
    """ "hyphen" or "joined": how the book prints the word made of ``pieces`` ("co" +
    "ordinated"), if its vocabulary says - the form an ``ocr_spacing`` correction restores.
    """
    return _established_form([Token(p) for p in pieces], vocab)


def _established_form(split: Sequence[Token], vocab: Counter) -> Optional[str]:
    """ "hyphen" or "joined": how the book prints the word ``split`` spells, if it says."""
    joined = vocab["".join(t.text for t in split).lower()]
    hyphenated = vocab["-".join(t.text for t in split).lower()]
    if hyphenated > joined and hyphenated >= 2:
        return "hyphen"
    if joined > hyphenated and joined >= 2:
        return "joined"
    return None


def _possessive(word: str) -> str:
    return word[:-2] if word.endswith("'s") else word


def _single_words(
    html_part: Sequence[Token], pdf_part: Sequence[Token]
) -> Optional[Tuple[str, str]]:
    """(HTML word, PDF word), lower-cased, when both sides are ONE word with no digit (a unit
    designation such as "11th" / "111th" is a claim) and they are not possessive variants
    ("Johnson" / "Johnson's": both valid, the sentence decides); else None."""
    if len(html_part) != 1 or len(pdf_part) != 1:
        return None
    h_word, p_word = html_part[0].text.lower(), pdf_part[0].text.lower()
    if any(c.isdigit() for c in h_word + p_word) or _ROMAN.match(html_part[0].text):
        return None
    if _ROMAN.match(pdf_part[0].text) or _possessive(h_word) == _possessive(p_word):
        return None
    return h_word, p_word


def _by_frequency(
    html_part: Sequence[Token], pdf_part: Sequence[Token], vocab: Optional[Counter]
) -> Optional[str]:
    """Decide a single-word difference from the WHOLE book: the established reading wins.
    Numbers and Roman numerals are never decided this way (both readings can be common).
    """
    pair = _single_words(html_part, pdf_part) if vocab else None
    if pair is None or vocab is None:
        return None
    return _frequency_verdict(vocab[pair[0]], vocab[pair[1]])


def _by_second_reading(
    html_part: Sequence[Token], pdf_part: Sequence[Token], second: Counter
) -> Optional[str]:
    """``second``: the words of the SAME pages read by another OCR of the same authoritative
    images (the PDF's 2010 text layer, when the PDF reading is Chandra's). Where it has the
    transcription's word and never the PDF reading's ("Schwarzwalder" vs Chandra's
    "Schwarzwaldor"), the PDF reading is an OCR error: ``ocr_crossread``. Where it has the PDF
    reading and never the transcription's, two independent OCRs agree on what the print says:
    ``html_error``."""
    pair = _single_words(html_part, pdf_part)
    if pair is None or _unaccented(pair[0]) == _unaccented(pair[1]):
        return None  # the 2010 layer has no accents: it cannot judge one
    h, p = second[pair[0]], second[pair[1]]
    if bool(h) == bool(p):
        return None
    return "ocr_crossread" if h else "html_error"


def _frequency_verdict(h: int, p: int) -> Optional[str]:
    """``h``/``p``: whole-book counts of the HTML / PDF reading (the PDF's count includes the
    disputed token itself, so an absent HTML reading needs the PDF's to recur 3 times).
    """
    if p <= 2 and h >= 3 and h >= 10 * max(p, 1):
        return "ocr_frequency"
    if (h <= 2 and p >= 3 and p >= 10 * max(h, 1)) or (h == 0 and p >= 3):
        # the transcription's reading is rare or ABSENT from the book; the PDF's recurs
        return "html_error"
    return None


# --- alignment ---------------------------------------------------------------------------------------


@dataclass
class Difference:
    cls: str
    html: str
    pdf: str
    pdf_page: Optional[int]
    printed_page: Optional[int]
    bbox: Optional[Tuple[float, ...]]
    context: str
    # (start, end) of the PDF reading in the source markdown, when every token has a position
    span: Optional[Tuple[int, int]] = None


@dataclass
class CrossCheck:
    stream: str
    html_words: int
    pdf_words: int
    aligned_words: int
    differences: List[Difference] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        return dict(Counter(d.cls for d in self.differences))

    def _share(self, classes) -> float:
        real = [d for d in self.differences if d.cls not in NOT_TEXT]
        if not real:
            return 1.0
        return sum(1 for d in real if d.cls in classes) / len(real)

    @property
    def correctable_share(self) -> float:
        """Differences that are PDF OCR artefacts with a deterministic correction."""
        return self._share(CORRECTABLE)

    @property
    def decided_share(self) -> float:
        """Differences settled without a human: correctable OCR + transcription errors."""
        return self._share(CORRECTABLE + PDF_RIGHT)


def crosscheck(
    stream: str,
    html_tokens: List[Token],
    pdf_tokens_: List[Token],
    context: int = 6,
    vocab: Optional[Counter] = None,
    second: Optional[Counter] = None,
) -> CrossCheck:
    """Align the two word streams and classify every difference (``classify``)."""
    sm = difflib.SequenceMatcher(
        None,
        [t.text.lower() for t in html_tokens],
        [t.text.lower() for t in pdf_tokens_],
        autojunk=False,
    )
    result = CrossCheck(
        stream,
        len(html_tokens),
        sum(1 for t in pdf_tokens_ if t.kind != "layout"),
        sum(b.size for b in sm.get_matching_blocks()),
    )
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            continue
        for h_part, p_part, offset in _split_differences(
            html_tokens[a1:a2], pdf_tokens_[b1:b2]
        ):
            diff = _difference(h_part, p_part, html_tokens, a1 + offset, context)
            diff.cls = classify(h_part, p_part, vocab, second)
            result.differences.append(diff)
    return result


def _split_differences(
    h: List[Token], p: List[Token]
) -> Iterable[Tuple[List[Token], List[Token], int]]:
    """A replace of equal length is several word-for-word differences (OCR errors a few words
    apart): pair them up - when every pair is a plausible misreading of its partner; words
    that do not correspond keep the block whole. Yields (html, pdf, html offset).
    """
    pairs = [
        (i, x, y)
        for i, (x, y) in enumerate(zip(h, p))
        if x.text.lower() != y.text.lower()
    ]
    if (
        len(h) == len(p)
        and len(h) > 1
        and all(_similar(x.text, y.text) for _i, x, y in pairs)
    ):
        for i, x, y in pairs:
            yield [x], [y], i
        return
    yield h, p, 0


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.5


def _difference(h_part, p_part, html_all, a1, context) -> Difference:
    anchor = p_part[0] if p_part else None
    before = " ".join(t.text for t in html_all[max(0, a1 - context) : a1])
    end = a1 + len(h_part)
    after = " ".join(t.text for t in html_all[end : end + context])
    return Difference(
        cls="",
        html=" ".join(t.text for t in h_part),
        pdf=" ".join(t.text for t in p_part),
        pdf_page=anchor.page if anchor else None,
        printed_page=(
            anchor.printed if anchor else (h_part[0].printed if h_part else None)
        ),
        bbox=anchor.bbox if anchor else None,
        context=f"{before} [...] {after}".strip(),
        span=_part_span(p_part),
    )


def _part_span(p_part: Sequence[Token]) -> Optional[Tuple[int, int]]:
    spans = [t.span for t in p_part]
    if not spans or any(s is None for s in spans):
        return None
    return (spans[0][0], spans[-1][1])  # type: ignore[index]


def _one_sided(checks: Sequence[CrossCheck], cls: str) -> List[Difference]:
    """``cls`` differences of at least three words (shorter ones pair up by chance)."""
    side = "html" if cls == "html_only" else "pdf"
    return [
        d
        for check in checks
        for d in check.differences
        if d.cls == cls and len(getattr(d, side).split()) >= 3
    ]


def _moved_words(text: str) -> List[str]:
    return text.lower().split()


def _overlaps(pdf_words: List[str], html_diff: Difference) -> bool:
    """The shorter side is a contiguous WORD run of the longer, covering >= 80% of it: a
    3-word fragment inside a 10-word block is not the same text moved."""
    theirs = _moved_words(html_diff.html)
    short, long_ = sorted((pdf_words, theirs), key=len)
    if len(short) < 0.8 * len(long_):
        return False
    return f" {' '.join(short)} " in f" {' '.join(long_)} "


def reconcile_moved(checks: Sequence[CrossCheck]) -> Dict[str, int]:
    """Text that is ``pdf_only`` in one stream and ``html_only`` in another (or the same) stream
    is the same text placed differently - a note continued onto the next page reads as narrative
    in the PDF. Reclassify both sides as ``moved``; each ``html_only`` pairs at most once.
    Returns the pairs found, ``across`` streams and ``within`` one (a transposition)."""
    stream_of = {id(d): c.stream for c in checks for d in c.differences}
    html_side = _one_sided(checks, "html_only")
    found = {"across": 0, "within": 0}
    for d in _one_sided(checks, "pdf_only"):
        mine = _moved_words(d.pdf)
        match = next(
            (h for h in html_side if h.cls == "html_only" and _overlaps(mine, h)), None
        )
        if match is not None:
            match.cls = d.cls = "moved"
            same = stream_of[id(match)] == stream_of[id(d)]
            found["within" if same else "across"] += 1
    return found


def grouped_reviews(
    check: CrossCheck,
) -> List[Tuple[str, str, int, List[Optional[int]]]]:
    """Review items grouped by (HTML reading, PDF reading), most frequent first: a systematic
    difference (HyperWar's map numbers "VIII" vs the book's "VII") is one finding, not many.
    """
    groups: Dict[Tuple[str, str], List[Optional[int]]] = {}
    for d in check.differences:
        if d.cls in NEEDS_A_LOOK:
            groups.setdefault((d.html, d.pdf), []).append(d.printed_page)
    rows = [(h, p, len(pages), pages) for (h, p), pages in groups.items()]
    return sorted(rows, key=lambda r: -r[2])


def summary(check: CrossCheck) -> Dict[str, Any]:
    """Counts and shares. ``aligned_share`` (aligned / HTML words) guards the rate: a stream
    that failed to align (wrong pages, an empty stream) has few differences but a low share.
    """
    real = sum(n for c, n in check.counts.items() if c not in NOT_TEXT)
    return {
        "stream": check.stream,
        "html_words": check.html_words,
        "pdf_words": check.pdf_words,
        "aligned_words": check.aligned_words,
        "aligned_share": round(check.aligned_words / max(check.html_words, 1), 3),
        "differences": check.counts,
        "real_differences": real,
        "per_1000_words": round(1000 * real / max(check.html_words, 1), 1),
        "correctable_share": round(check.correctable_share, 3),
        "decided_share": round(check.decided_share, 3),
    }
