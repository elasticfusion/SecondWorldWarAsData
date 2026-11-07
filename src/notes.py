"""Separate a chapter's footnotes/endnotes from its narrative text (Phase 1), keeping provenance.

Owner rule (2026-11-07): Phase 1 SEPARATES the notes from the text so they can be evaluated (and
submitted to Grok) on their own, with provenance back to the note number, its kind, its source URL
and the paragraph that cites it. Before this, a converted HyperWar chapter kept its notes as raw HTML
anchors (``<sup><a href="#fn1" id="cn1">1</a></sup>`` and ``\\[<a href="#cn1" id="fn1">1</a>\\] text``)
that no Phase 1/2 regex recognised: 0 notes were parsed, and the whole ``### Footnotes`` section
(~100 notes + the page navigation) became ONE 27k-character "narrative" paragraph that every Phase 2
extraction prompt then carried (XChannel-8 E2E).

``separate_notes`` returns the body WITHOUT the notes section / navigation chrome, with every
in-text note marker rewritten to the ONE canonical form the rest of the pipeline already reads
(``<sup>[N](<source_url>#fnN)</sup>``), plus the parsed notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

# A notes section heading (optionally block-quoted / emphasised): "### Footnotes", "> ## Notes".
_HEADING = re.compile(
    r"^[ \t]{0,3}(?:>[ \t]?){0,3}#{1,6}[ \t]*\*{0,2}(Footnotes|Endnotes|Notes)\*{0,2}[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
# A candidate site-navigation line: "[Table of Contents](index.html) ** [Previous Chapter]...".
# It is removed only when it carries at least _NAV_MIN_LINKS such links (see _strip_nav), so a
# narrative sentence that happens to quote one link is kept.
_NAV = re.compile(
    r"^[ \t]{0,3}(?:>[ \t]?){0,3}.{0,40}\[(?:Table of Contents|Previous Chapter|Next Chapter)\]"
    r"\([^)\s]{0,300}\).{0,400}$",
    re.IGNORECASE | re.MULTILINE,
)
_NAV_LINK = re.compile(
    r"\[(?:Table of Contents|Previous Chapter|Next Chapter)\]\(", re.IGNORECASE
)
_NAV_MIN_LINKS = 2
# In-text marker as a raw HTML anchor (pandoc passthrough), quoted or unquoted attributes.
_HTML_MARKER = re.compile(
    r"<sup>\s{0,5}<a\b[^>]{0,300}?href=[\"']?#fn(\d{1,6})[\"']?[^>]{0,300}>\s{0,5}\d{1,6}"
    r"\s{0,5}</a>\s{0,5}</sup>",
    re.IGNORECASE,
)
# A note DEFINITION line, after any leading blockquote markers are removed:
#   HTML:      \[<a href="#cn1" id="fn1">1</a>\] text      (also [<a name=fn1 href=#cn1>1</a>] text)
#   markdown:  [1](https://host/page.html#cn1). text
_DEF_HTML = re.compile(
    r"^\\?\[\s{0,3}<a\b[^>]{0,300}?#cn(\d{1,6})[^>]{0,300}>\s{0,3}\d{1,6}\s{0,3}</a>\s{0,3}\\?\]"
    r"\s{0,5}(.*)$",
    re.IGNORECASE,
)
_DEF_MD = re.compile(
    r"^\[(\d{1,6})\]\((https?://[^)\s\"]{0,2000}#cn\d{1,6})\)\.?\s{0,5}(.*)$"
)
_QUOTE_PREFIX = re.compile(r"^[ \t]{0,3}(?:>[ \t]?){1,3}")
# Pandoc backslash-escapes punctuation when converting HTML ("\[Clifford Jones\]", "MS \# B-432").
# Those escapes are conversion artefacts, not source text: remove them from the note text.
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
# The section ends at a horizontal rule that is NOT block-quoted (a whole-line rule, so a note
# starting "***bold***" does not end it), or the HyperWar trailer.
_SECTION_END = re.compile(
    r"^(?:-{10,}[ \t]*$|\*{3,}[ \t]*$|_{3,}[ \t]*$|Transcribed and formatted)",
    re.IGNORECASE,
)
# Markers in the canonical form (used to find the paragraph that cites each note).
CANONICAL_MARKER = re.compile(r"<sup>\[(\d{1,6})\]")


@dataclass
class Note:
    """One footnote/endnote, with provenance."""

    number: int
    kind: str  # "footnote" | "endnote" (from the source's own section heading)
    text: str
    url: str = ""  # <source_url>#fnN (where the note text lives), "" if unknown
    paragraph_number: int = (
        0  # absolute number of the first paragraph citing it (0 = unknown)
    )


@dataclass
class Separation:
    """What ``separate_notes`` found, beyond the notes themselves (for the parser's OBSERV/alerts)."""

    duplicates: List[int]  # note numbers defined more than once (first definition kept)
    nav_lines_removed: int


def _kind_for(heading: str) -> str:
    """Use the SOURCE's own label: a 'Footnotes' section holds footnotes; 'Notes'/'Endnotes' hold
    endnotes."""
    return "footnote" if heading.lower().startswith("foot") else "endnote"


def _http_base(url: str) -> str:
    base = (url or "").split("#", 1)[0]
    return base if base.lower().startswith(("http://", "https://")) else ""


def _note_url(base_url: str, number: int) -> str:
    base = _http_base(base_url)
    return f"{base}#fn{number}" if base else ""


def _match_definition(line: str, base_url: str) -> Optional[Tuple[int, str, str]]:
    """(number, text, note_url) for a definition line, else None. A markdown definition carries
    its own page URL, used when the caller has no http(s) base."""
    m = _DEF_HTML.match(line)
    own_url = ""
    if not m:
        m = _DEF_MD.match(line)
        if not m:
            return None
        own_url = m.group(2)
    number = int(m.group(1))
    text = _MD_ESCAPE.sub(r"\1", m.group(m.lastindex or 1) or "").strip()
    return number, text, _note_url(base_url, number) or _note_url(own_url, number)


def _parse_section(
    lines: List[str], kind: str, base_url: str
) -> Tuple[Dict[int, Note], List[int]]:
    """Parse definition lines (with continuation lines) into notes keyed by number, plus the
    numbers defined more than once. A repeated number keeps its FIRST definition: merging two
    texts under one number would be false provenance."""
    notes: Dict[int, Note] = {}
    duplicates: List[int] = []
    current: Optional[Note] = None
    for raw in lines:
        line = _QUOTE_PREFIX.sub("", raw).strip()
        if not line:
            current = None  # a blank (or bare '>') line ends a note
            continue
        found = _match_definition(line, base_url)
        if found:
            number, text, url = found
            if number in notes:
                duplicates.append(number)
                current = Note(number, kind, text, url)  # absorbs its lines; not kept
            else:
                current = notes[number] = Note(number, kind, text, url)
            continue
        if current is not None:  # continuation of a multi-line note
            cont = _MD_ESCAPE.sub(r"\1", line)
            current.text = f"{current.text} {cont}".strip()
    return notes, duplicates


def _section_end(content: str, start: int) -> int:
    """Offset where the notes section starting at ``start`` ends: the first unquoted horizontal
    rule / trailer line, or the end of the text."""
    offset = start
    for line in content[start:].splitlines(keepends=True):
        if _SECTION_END.match(line):
            return offset
        offset += len(line)
    return len(content)


def _find_section(
    content: str, base_url: str
) -> Optional[Tuple[int, int, Dict[int, Note], List[int]]]:
    """(start, end, notes, duplicates) of the notes section: the LAST heading whose section really
    holds note definitions. Later headings without definitions (a narrative "Notes on Sources", an
    appendix) are skipped instead of hiding the real section."""
    for head in reversed(list(_HEADING.finditer(content))):
        end = _section_end(content, head.end())
        lines = content[head.start() : end].splitlines()[1:]
        notes, duplicates = _parse_section(lines, _kind_for(head.group(1)), base_url)
        if notes:
            return head.start(), end, notes, duplicates
    return None


def _strip_nav(text: str) -> Tuple[str, int]:
    removed = 0

    def _sub(m: "re.Match[str]") -> str:
        nonlocal removed
        if len(_NAV_LINK.findall(m.group(0))) < _NAV_MIN_LINKS:
            return m.group(0)
        removed += 1
        return ""

    return _NAV.sub(_sub, text), removed


def canonicalize_markers(text: str, base_url: str) -> str:
    """Rewrite raw-HTML in-text note markers to ``<sup>[N](<base>#fnN)</sup>`` (or ``<sup>[N]</sup>``
    when there is no http(s) base) - the single form the parser, events and supplemental read.
    """

    def _sub(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        url = _note_url(base_url, n)
        return f"<sup>[{n}]({url})</sup>" if url else f"<sup>[{n}]</sup>"

    return _HTML_MARKER.sub(_sub, text)


def separate_notes_detailed(
    content: str, base_url: str = ""
) -> Tuple[str, List[Note], Separation]:
    """Split ``content`` into (body, notes, separation details). The body has the notes section and
    navigation lines removed and its markers canonicalised; notes are ordered by number.
    """
    found = _find_section(content, base_url)
    body = content
    notes: Dict[int, Note] = {}
    duplicates: List[int] = []
    if found:
        start, end, notes, duplicates = found
        body = content[:start] + content[end:]
    body, nav_removed = _strip_nav(body)
    body = canonicalize_markers(body, base_url)
    return (
        body,
        [notes[n] for n in sorted(notes)],
        Separation(sorted(set(duplicates)), nav_removed),
    )


def separate_notes(content: str, base_url: str = "") -> Tuple[str, List[Note]]:
    """``separate_notes_detailed`` without the details."""
    body, notes, _details = separate_notes_detailed(content, base_url)
    return body, notes


def first_citing_paragraphs(paragraphs: Iterable) -> Dict[int, int]:
    """note number -> absolute number of the first paragraph whose text carries its canonical
    marker."""
    first: Dict[int, int] = {}
    for p in paragraphs:
        for m in CANONICAL_MARKER.finditer(getattr(p, "text", "") or ""):
            first.setdefault(int(m.group(1)), p.absolute_number)
    return first


def attach_citing_paragraphs(notes: Iterable, paragraphs: Iterable) -> int:
    """Set each note's ``paragraph_number`` (any object with ``number`` / ``paragraph_number``:
    a Note or a models.Footnote) to the first paragraph citing it. Returns how many were
    located; unlocated notes keep their value."""
    first = first_citing_paragraphs(paragraphs)
    located = 0
    for note in notes:
        if note.number in first:
            note.paragraph_number = first[note.number]
            located += 1
    return located
