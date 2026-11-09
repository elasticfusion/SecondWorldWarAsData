"""Split an OCR'd book (Chandra, paginated) into its chapters, for Phase 1.

A whole scanned book used to be promoted as ONE chapter (``chapter1``): 562 pages of
Cross-Channel Attack as one parse unit, one event extraction and one citation scope. Citation is
by chapter and paragraph number (owner 2026-11-08), so the book is split at its own chapter
headings, which Chandra reads reliably ("## CHAPTER VIII" followed by "# The Sixth of June",
"# Appendix A", "# Bibliographical Note").

Each section is published as ``chapterN`` (the book's own chapter number; appendices numbered
from their letter after the last chapter - Appendix A is the last chapter + 1, so a missed
appendix heading never renumbers the others - and the bibliographical note after the last
appendix), or held back: the front matter
(foreword, preface, contents), the glossary and the index are reference apparatus, not
narrative - they stay in the raw OCR merge, which is kept, and are listed in the log. A document
with no chapter headings (an after-action report, a journal) is one section, ``chapter1``, as
before. A chapter may start mid-page (CHAPTER IV on PDF page 146): the page then belongs to both
sections, each carrying its page anchor.

Pure: no AWS, no model calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# The numeral is read loosely and judged by ``_roman_value`` ("CHAPTER XIl" is an OCR slip, not
# chapter 59): a heading with an unreadable number is held back and reported, never numbered.
_CHAPTER = re.compile(
    r"^#{1,3}[ \t]+(?i:chapter)[ \t]+([IVXLCivxlc1|!0-9]{1,8})[ \t]*$", re.M
)
_CANONICAL_ROMAN = re.compile(r"^(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$|^C$")
_APPENDIX = re.compile(r"^#{1,3}[ \t]+APPENDIX[ \t]+([A-Z]|\d{1,2})\b.*$", re.I | re.M)
# Book apparatus at the top heading level only (an in-chapter "### Notes" is not one).
_MATTER = re.compile(
    r"^#[ \t]+(Foreword|Preface|Contents|Introductory Note\b.*|Introduction|Glossary"
    r"|Bibliographical Note|Bibliography|Note on Sources|Index|Abbreviations)[ \t]*$",
    re.I | re.M,
)
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.M)
# Apparatus that is narrative enough to extract from (events, sources); the rest is held back.
_PUBLISHED_MATTER = ("bibliographical note", "bibliography", "note on sources")
PAGE_ANCHOR = '<a id="page{}"></a>'
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_MAX_LETTER_APPENDICES = 26


@dataclass
class Section:
    kind: str  # front | chapter | appendix | matter
    label: str  # "CHAPTER VIII", "Appendix A", "Glossary", "Front matter"
    title: str  # the book's title of the section ("The Sixth of June"), "" if none
    first_page: Optional[int]
    last_page: Optional[int]
    text: str  # the section's markdown, page anchors included
    number: int = 0  # published chapter number; 0 = held back
    warnings: List[str] = field(
        default_factory=list
    )  # anything odd found in this section

    @property
    def published(self) -> bool:
        return self.number > 0

    @property
    def chapter_title(self) -> str:
        if self.kind == "chapter":
            return self.title or self.label
        return f"{self.label}: {self.title}" if self.title else self.label


def paginated_book(parts: Sequence[Tuple[Optional[int], str]]) -> str:
    """One markdown text of the book's OCR parts (``(first PDF page, chunk markdown)`` in page
    order), each page opened by its anchor ``<a id="pageN"></a>`` (N = PDF page). A part with
    no known first page is numbered on from the previous part."""
    from src.ingestion.chunk_pages import split_pages

    out: List[str] = []
    next_page = 1
    for first, markdown in parts:
        start = first if first is not None else next_page
        for page, text in split_pages(markdown, start):
            out.append(f"{PAGE_ANCHOR.format(page)}\n\n{text.strip()}\n")
            next_page = page + 1
    return "\n".join(out)


_ANCHOR = re.compile(r'<a id="page(\d+)"></a>')
_TRAILING_ANCHORS = re.compile(r'(?:\s*<a id="page\d+"></a>)+\s*$')
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")


def split_book(book_markdown: str) -> List[Section]:
    """The book's sections in order (see the module docstring for what is published)."""
    starts, ignored = _section_starts(book_markdown)
    if not any(kind == "chapter" for _, kind, _ in starts):
        return [_whole(book_markdown)]
    sections: List[Section] = []
    if _ANCHOR.sub("", book_markdown[: starts[0][0]]).strip():
        starts.insert(0, (0, "front", "Front matter"))
    for i, (pos, kind, label) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(book_markdown)
        sections.append(_section(book_markdown, pos, end, kind, label))
    _note_ignored(sections, starts, ignored)
    _number(sections)
    return sections


def _note_ignored(
    sections: List[Section], starts: list, ignored: List[Tuple[int, str]]
) -> None:
    """A repeated heading (a running head) stays in the text of the section it falls in; say so."""
    for pos, label in ignored:
        inside = [i for i, (p, _k, _l) in enumerate(starts) if p <= pos]
        if inside:
            sections[inside[-1]].warnings.append(
                f"repeated heading {label!r} kept as text"
            )


def _section_starts(
    markdown: str,
) -> Tuple[List[Tuple[int, str, str]], List[Tuple[int, str]]]:
    """(section starts, headings ignored). A chapter or appendix heading that repeats an earlier
    one is a running head, not a new section."""
    found = [
        (m.start(), "chapter", f"CHAPTER {m.group(1).upper()}")
        for m in _CHAPTER.finditer(markdown)
    ]
    found += [
        (m.start(), "appendix", f"Appendix {m.group(1).upper()}")
        for m in _APPENDIX.finditer(markdown)
    ]
    found += [
        (m.start(), "matter", m.group(1).strip()) for m in _MATTER.finditer(markdown)
    ]
    found.sort()
    # front matter headings (Foreword, Contents ...) before the first chapter are one section
    first_chapter = next((p for p, k, _ in found if k == "chapter"), None)
    found = [s for s in found if first_chapter is None or s[0] >= first_chapter]
    found = _without_titles(markdown, found)
    return _without_repeats(found)


def _without_titles(markdown: str, found: list) -> list:
    """Drop a matter heading that is a chapter's or appendix's TITLE ("## CHAPTER I" then
    "# Introduction"): nothing but headings and page anchors lies between the two."""
    out: list = []
    for start in found:
        pos, kind, _label = start
        if kind == "matter" and out and out[-1][1] in ("chapter", "appendix"):
            between = markdown[out[-1][0] : pos]
            rest = _ANCHOR.sub("", re.sub(r"(?m)^#{1,6}[ \t].*$", "", between)).strip()
            if not rest:
                continue
        out.append(start)
    return out


def _without_repeats(found: list) -> Tuple[list, List[Tuple[int, str]]]:
    seen: set = set()
    keep, ignored = [], []
    for start in found:
        pos, kind, label = start
        if kind in ("chapter", "appendix"):
            if (kind, label) in seen:
                ignored.append((pos, label))
                continue
            seen.add((kind, label))
        keep.append(start)
    return keep, ignored


def _section(markdown: str, pos: int, end: int, kind: str, label: str) -> Section:
    body = _TRAILING_ANCHORS.sub(
        "", markdown[pos:end]
    )  # the next section's page opening
    before = list(_ANCHOR.finditer(markdown, 0, pos + 1))
    page = int(before[-1].group(1)) if before else None
    if page is not None and not body.lstrip().startswith('<a id="page'):
        body = f"{PAGE_ANCHOR.format(page)}\n\n{body}"  # a chapter opening mid-page
    pages = [int(m.group(1)) for m in _ANCHOR.finditer(body)]
    return Section(
        kind,
        label,
        _title(body, kind),
        pages[0] if pages else page,
        pages[-1] if pages else page,
        body.strip() + "\n",
    )


def _title(body: str, kind: str) -> str:
    """The heading right after the section's label heading ("# The Sixth of June"): the next
    non-blank line, when it is a heading (Appendix C opens with its document, not a title).
    """
    if kind not in ("chapter", "appendix"):
        return ""
    lines = [
        ln.strip() for ln in _ANCHOR.sub("", body[:2000]).splitlines() if ln.strip()
    ]
    if len(lines) < 2 or not _HEADING.match(lines[1]):
        return ""
    title = _HEADING.match(lines[1]).group(1)  # type: ignore[union-attr]
    return _MD_ESCAPE.sub(r"\1", title).strip("*_ ").rstrip("*").strip()


def _number(sections: List[Section]) -> None:
    """Chapters keep the book's own number; appendices follow from their letter; the published
    apparatus (the bibliographical note) follows the last appendix. A heading whose number
    cannot be read is held back with a warning."""
    last = 0
    for s in sections:
        if s.kind == "chapter":
            value = _roman_value(s.label.split()[-1])
            if value is None:
                s.warnings.append(
                    f"unreadable chapter number in {s.label!r}: held back"
                )
            else:
                s.number = value
                last = max(last, value)
    first_appendix = last
    for s in sections:
        if s.kind == "appendix":
            index = _appendix_index(s.label.split()[-1])
            if index is None:
                s.warnings.append(
                    f"unreadable appendix label in {s.label!r}: held back"
                )
                continue
            s.number = first_appendix + index
            last = max(last, s.number)
    for s in sections:
        if s.kind == "matter" and s.label.lower() in _PUBLISHED_MATTER:
            last += 1
            s.number = last


def _appendix_index(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token) if int(token) > 0 else None
    if len(token) == 1 and token.isalpha():
        return ord(token.upper()) - ord("A") + 1
    return None


def _roman_value(token: str) -> Optional[int]:
    """The value of an arabic number or a CANONICAL roman numeral, else None."""
    if token.isdigit():
        return int(token) if int(token) > 0 else None
    token = token.upper()
    if not _CANONICAL_ROMAN.match(token) or not token:
        return None
    total = 0
    for i, ch in enumerate(token):
        value = _ROMAN[ch]
        nxt = _ROMAN[token[i + 1]] if i + 1 < len(token) else 0
        total += -value if value < nxt else value
    return total


def _whole(markdown: str) -> Section:
    pages = [int(m.group(1)) for m in _ANCHOR.finditer(markdown)]
    return Section(
        "chapter",
        "",
        "",
        pages[0] if pages else None,
        pages[-1] if pages else None,
        markdown.strip() + "\n",
        1,
    )
