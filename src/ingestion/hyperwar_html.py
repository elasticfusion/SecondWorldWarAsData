"""Normalise ibiblio HyperWar HTML before pandoc, so each source paragraph is ONE markdown paragraph.

HyperWar transcriptions (https://www.ibiblio.org/hyperwar/) wrap every PRINTED PAGE in a
``<blockquote>``, separate paragraphs with bare (never closed) ``<p>`` tags, and end each page with
a footer ``<p><center><i>--269--</i></center><hr><a name=page270></a><p>``. Converted as-is, pandoc
emits one quote block per page with the paragraph breaks as ``>`` lines, the footer as ``*--269--*``
and a rule, and the parser (blank-line blocks) read a whole page as one "paragraph" and every
marker and rule as paragraphs: XChannel-8 had 262 "paragraphs", and 50 of its 64 pages end
mid-sentence, so a paragraph crossing a page was cut in two.

The page is parsed with BeautifulSoup + **html5lib**, the HTML5 parsing algorithm browsers use:
bare ``<p>`` tags are closed, missing ``</tr>`` are supplied, misnested markup is repaired - the
tree is what a reader of the page sees. (Python's ``html.parser`` applies none of these rules: bare
``<p>`` tags nest the whole page.) Then, for HTML with the HyperWar page-footer signature only:

* each page footer becomes ONE page anchor ``<a id="pageN"></a>`` (N = the next page: its anchor,
  else the label + 1 - labels can be mistyped, "--27--" for 275);
* the page-frame ``<blockquote>`` wrappers and ``<hr>`` rules are removed;
* every paragraph is its own ``<p>`` (text written directly in the frame, before its first
  ``<p>``, is a paragraph too);
* a paragraph cut by page breaks is REJOINED when the text proves it continues (the page ends
  mid-sentence, or the next page begins in lower case); the page anchors stay INSIDE it (page
  provenance kept) and the figures between the halves move after it;
* a NESTED ``<blockquote>`` next to an image or a table is its caption / notes (Logistics1:
  "GENERAL DEPOT AT ASHCHURCH, above...", "a. By ship ... Source: ..."); any other nested
  blockquote is a QUOTATION and stays one (CBI-Mission: Stilwell's orders, the Generalissimo's
  telegram) - the parser marks it ``is_quote``;
* tables: a first row of ``<th>`` cells becomes the header (else pandoc writes an empty one);
* ``<font>`` is unwrapped (small caps: ``B<font size=-1>OLERO</font>`` is "BOLERO").

Two repairs run on the raw text first because no parser can recover them: page anchors left
unclosed or missing their ``>`` (``<a name=page315`` would otherwise wrap the next paragraphs in a
link, as a browser does) are closed, and the transcription typo ``<,br>`` is a ``<br>``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_LABEL_TEXT = re.compile(r"\s*--\s*(\d+)(?:\s*/\s*(\d+))?\s*--\s*")
_LABEL_HTML = re.compile(r"<i>\s*--\s*\d+(?:\s*/\s*\d+)?\s*--\s*</i>", re.I)
_PAGE_NAME = re.compile(r"^page(\d+)$", re.I)
# A page anchor, closed or not, even missing its ">" (<a name=page315\n<p>), with any further
# WELL-FORMED attributes consumed (they must not leak into the text: <a name=page3 id=zz>).
_RAW_PAGE_ANCHOR = re.compile(
    r"<a\s+name\s*=\s*[\"']?page(\d+)[\"']?"
    r"(?:\s+[\w:-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s<>\"']+))*\s*>?(?:\s*</a\s*>)?",
    re.I,
)
_BROKEN_BR = re.compile(r"<\s*,\s*br\s*>", re.I)

_INLINE = {
    "a", "abbr", "b", "br", "cite", "code", "em", "font", "i", "img", "small", "span",
    "strong", "sub", "sup", "u", "q", "s", "tt", "var",
}  # fmt: skip
_LISTS = {"ol", "ul", "dl"}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_TERMINAL = set(".!?:\"\u201d\u2019')]\u2026\u00bb*")
# A lower-case word that begins a NAME ("von Rundstedt", "de Gaulle") is not a continuation.
_NAME_PARTICLES = {"von", "van", "de", "der", "den", "du", "da", "di", "la", "le", "d'"}
_NOTE_SUP = re.compile(r"<sup\b[^>]*>.*?</sup\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]*>")
# Kinds a paragraph may continue ACROSS (moved after the joined paragraph) vs boundaries.
_SKIPPABLE = ("page", "figure")


class HyperWarParserUnavailable(RuntimeError):
    """html5lib is not installed (it is a pinned dependency: requirements.txt)."""


def is_hyperwar_html(html: str) -> bool:
    """True for a HyperWar transcription: page labels (``<i>--N--</i>``) and page anchors."""
    text = html or ""
    return len(_LABEL_HTML.findall(text)) >= 2 and bool(_RAW_PAGE_ANCHOR.search(text))


@dataclass
class _Block:
    kind: str  # text | figure | caption | quote | heading | list | page
    html: str
    page: Optional[int] = None


@dataclass
class NormaliseReport:
    pages: int = 0
    joined: int = 0
    kept_apart: int = 0
    figures_moved: int = 0
    paragraphs: int = 0
    quotes: int = 0
    captions: int = 0
    notes: int = (
        0  # note anchors (markers + definitions) rewritten to the canonical form
    )
    labels_without_anchor: List[int] = field(default_factory=list)
    labels: int = (
        0  # page labels in the source (pages < labels = some footers not split)
    )


# --- tree -> blocks -------------------------------------------------------------------------------


def _is_label(node: Any) -> Optional[re.Match]:
    if getattr(node, "name", None) != "center":
        return None
    return _LABEL_TEXT.fullmatch(node.get_text())


def _page_anchor(node: Any) -> Optional[int]:
    if getattr(node, "name", None) != "a":
        return None
    m = _PAGE_NAME.match(str(node.get("name") or ""))
    return int(m.group(1)) if m else None


def _holds_labels(node: Any) -> bool:
    return any(_is_label(c) for c in node.find_all("center"))


_BLOCK_TAGS = [
    "p", "blockquote", "center", "table", "ol", "ul", "dl", "div", "address",
    "h1", "h2", "h3", "h4", "h5", "h6",
]  # fmt: skip


def _follows_page_break(node: Any) -> bool:
    """A blockquote directly after a page label or anchor IN THE SAME PARENT is the next page's
    frame nested in an unclosed one (a quotation at a page top sits inside its own frame).
    """
    prev = node.find_previous_sibling(True)
    while prev is not None and prev.name == "p" and not _has_content(str(prev)):
        prev = prev.find_previous_sibling(True)
    return prev is not None and (
        _is_label(prev) is not None or _page_anchor(prev) is not None
    )


def _unwrap_inline_around_blocks(soup: Any) -> None:
    """An inline element holding block content (an ``<i>`` left open: the HTML5 algorithm puts the
    following paragraphs INSIDE it) is markup error, not structure: unwrap it so its paragraphs
    stay paragraphs."""
    for el in soup.find_all(list(_INLINE)):
        if el.parent is not None and el.find(_BLOCK_TAGS):
            el.unwrap()


def _expose_page_breaks(soup: Any) -> None:
    """An inline tag left open before a page footer (``<i>`` never closed) is re-opened by the
    HTML5 algorithm around every later block, so the labels and page anchors of the rest of the
    book end up INSIDE it and the walker would read them as one inline run. Unwrap any inline
    element that encloses a label or a page anchor (its formatting was a markup error).
    """
    breaks = [c for c in soup.find_all("center") if _is_label(c)]
    breaks += soup.find_all("a", attrs={"name": _PAGE_NAME})
    for node in breaks:
        for parent in list(node.parents):
            if getattr(parent, "name", None) in _INLINE and parent is not node:
                parent.unwrap()


def _drop_markup_only_nodes(soup: Any) -> None:
    """Comments, doctypes, CDATA and processing instructions are not text: serialised as a string
    a comment's body would become live markup and visible words."""
    from bs4.element import CData, Comment, Declaration, Doctype, ProcessingInstruction

    kinds = (Comment, CData, Declaration, Doctype, ProcessingInstruction)
    for node in soup.find_all(string=lambda s: isinstance(s, kinds)):
        node.extract()


def _has_content(html: str) -> bool:
    return bool(_TAG.sub("", html).strip()) or "<img" in html.lower()


def _inner(node: Any) -> str:
    return "".join(str(c) for c in node.contents)


class _Walker:
    """Body children in order -> blocks. Page footers live BETWEEN the page frames."""

    def __init__(self, report: NormaliseReport) -> None:
        self.report = report
        self.blocks: List[_Block] = []
        self.pending_label: Optional[re.Match] = None
        self.run: List[str] = []

    # page breaks
    def _page(self, number: int) -> None:
        self._flush()
        if self.blocks and self.blocks[-1].kind == "page":
            # a footer with two anchors (page306 + page307): one break, both anchors kept
            self.blocks[-1].html += f'<a id="page{number}"></a>'
            return
        self.blocks.append(_Block("page", f'<a id="page{number}"></a>', number))

    def _close_label(self) -> None:
        """A footer label with no page anchor after it: the next page is label + 1."""
        if self.pending_label is not None:
            m = self.pending_label
            self.pending_label = None
            self.report.labels_without_anchor.append(int(m.group(1)))
            self._page(int(m.group(2) or m.group(1)) + 1)

    # inline runs (text written directly in a frame, outside any <p>)
    def _flush(self) -> None:
        html = "".join(self.run).strip()
        self.run = []
        if _has_content(html):
            self._add("text", html)

    def _add(self, kind: str, html: str) -> None:
        if kind == "text" and not _TAG.sub("", html).strip():
            kind = "figure"  # an image-only paragraph is a figure
        self.blocks.append(_Block(kind, html))

    def body(self, body: Any) -> List[_Block]:
        for node in list(body.children):
            self._step(node, top=True)
        self._flush()
        self._close_label()
        return self.blocks

    def frame(self, frame: Any) -> None:
        for node in list(frame.children):
            self._step(node, top=False)
        self._flush()

    def _step(self, node: Any, top: bool) -> None:
        """One node of the body or of a page frame. A label or page anchor is a page break
        wherever it sits; a blockquote is a page frame at the top level, or when it holds page
        labels (a frame left unclosed nests the following pages inside it)."""
        label = _is_label(node)
        if label:
            self._flush()
            self._close_label()
            self.pending_label = label
            self.report.pages += 1
            return
        page = _page_anchor(node)
        if page is not None:
            self.pending_label = None
            self._page(page)
            return
        name = getattr(node, "name", None)
        if name == "blockquote" and (
            top or _holds_labels(node) or _follows_page_break(node)
        ):
            self._close_label()
            self.frame(node)
            return
        if name == "hr" or (
            self.pending_label is not None and not _has_content(str(node))
        ):
            return  # a rule, or the empty <p> around a footer
        self._close_label()
        self.node(node)

    def node(self, node: Any) -> None:
        name = getattr(node, "name", None)
        if name is None or name in _INLINE:
            self.run.append(str(node))
            return
        self._flush()
        kind, html = _block_of(node, name)
        if kind == "text":
            if _has_content(html):
                self._add("text", html)
        elif kind and (kind != "figure" or _has_content(html)):
            self.blocks.append(_Block(kind, html))


def _block_of(node: Any, name: str) -> tuple:
    """(kind, html) of a block-level element; kind None = dropped (a rule)."""
    if name == "hr":
        return None, ""
    if name == "p":
        return "text", _inner(node).strip()
    if name == "blockquote":
        return "quote", _inner(node).strip()
    if name in _HEADINGS or (name == "center" and node.find(list(_HEADINGS))):
        return "heading", str(node)
    if name in _LISTS:
        return "list", str(node)
    return "figure", str(node)  # center / table / div / address


# --- captions vs quotations -------------------------------------------------------------------------


def _shows_media(block: Optional[_Block]) -> bool:
    return (
        bool(block)
        and block.kind == "figure"
        and bool(re.search(r"<(?:img|table)\b", block.html, re.I))
    )


def _neighbour(blocks: List[_Block], i: int, step: int) -> Optional[_Block]:
    j = i + step
    while 0 <= j < len(blocks) and blocks[j].kind == "page":
        j += step
    return blocks[j] if 0 <= j < len(blocks) else None


def _classify_quotes(blocks: List[_Block], report: NormaliseReport) -> None:
    """A nested blockquote beside an image or a table is its caption / notes (a figure part);
    otherwise it is a quotation."""
    for i, blk in enumerate(blocks):
        if blk.kind != "quote":
            continue
        if _shows_media(_neighbour(blocks, i, -1)) or _shows_media(
            _neighbour(blocks, i, 1)
        ):
            blk.kind = "figure"
            blk.html = f"<div>{blk.html}</div>"
            report.captions += 1
        else:
            report.quotes += 1


# --- rejoin paragraphs cut by page breaks -------------------------------------------------------------


def _plain(html: str) -> str:
    return " ".join(_TAG.sub(" ", html).split())


def _continues(before: _Block, after: _Block) -> bool:
    """The page break cut a paragraph: the text before it does not end a sentence (a trailing
    note marker is ignored: "...in Normandy.<sup>22</sup>" ends one), or the text after it begins
    in lower case."""
    end = _plain(_NOTE_SUP.sub("", before.html))[-1:]
    if end and end not in _TERMINAL and not _is_heading_like(before):
        return True
    words = _plain(after.html).split()
    first = words[0] if words else ""
    return first[:1].islower() and first.lower() not in _NAME_PARTICLES


def _is_heading_like(block: _Block) -> bool:
    """A short paragraph that is all bold / all capitals is a run-in heading, not a sentence cut by
    the page ("<p><b>Section Title Without Period</b>")."""
    text = _plain(block.html)
    if not text or len(text.split()) > 12:
        return False
    bold = re.fullmatch(r"\s*<(b|strong)\b[^>]*>.*</\1>\s*", block.html, re.I | re.S)
    letters = [c for c in text if c.isalpha()]
    return bool(bold) or (bool(letters) and all(c.isupper() for c in letters))


def _rejoin(blocks: List[_Block], report: NormaliseReport) -> List[_Block]:
    """Merge a paragraph cut by page breaks and/or figures printed inside it (Logistics1 prints a
    table in mid-sentence: "...pointed out the manpower" | TABLE | "needs. Once more..."). Every
    page crossed keeps its anchor inside the paragraph; the figures between the parts are HELD and
    placed after the paragraph once it ends (it may cross several breaks). Headings, quotations
    and lists are boundaries."""
    out: List[_Block] = []
    held: List[_Block] = []
    i = 0
    while i < len(blocks):
        if blocks[i].kind in _SKIPPABLE and out and out[-1].kind == "text":
            i = _join_or_pass(blocks, i, out, held, report)
            continue
        out.extend(held)
        held.clear()
        out.append(blocks[i])
        i += 1
    out.extend(held)
    return out


def _join_or_pass(
    blocks: List[_Block],
    i: int,
    out: List[_Block],
    held: List[_Block],
    report: NormaliseReport,
) -> int:
    """At a run of page breaks / figures after a paragraph: join the paragraph that continues
    after it (figures held), or emit the run unchanged. Returns the next index."""
    j = _end_of_run(blocks, i)
    between = blocks[i:j]
    nxt = blocks[j] if j < len(blocks) and blocks[j].kind == "text" else None
    if nxt is not None and _continues(out[-1], nxt):
        _merge(out[-1], between, nxt, held, report)
        return j + 1
    if nxt is not None and any(b.kind == "page" for b in between):
        report.kept_apart += 1
    out.extend(held + between)
    held.clear()
    return j


def _end_of_run(blocks: List[_Block], i: int) -> int:
    while i < len(blocks) and blocks[i].kind in _SKIPPABLE:
        i += 1
    return i


def _merge(
    para: _Block,
    between: List[_Block],
    nxt: _Block,
    held: List[_Block],
    report: NormaliseReport,
) -> None:
    """Append ``nxt`` to ``para`` with the page anchors crossed; hold the figures between."""
    anchors = "".join(b.html for b in between if b.kind == "page")
    para.html = f"{para.html} {anchors}{nxt.html}"
    figures = [b for b in between if b.kind == "figure"]
    held.extend(figures)
    report.joined += 1
    report.figures_moved += len(figures)


# --- tables / emit -------------------------------------------------------------------------------------


def _promote_headers(soup: Any) -> None:
    """A first row of only ``<th>`` cells becomes the table's ``<thead>`` (pandoc then writes it
    as the markdown header instead of an empty one)."""
    for table in soup.find_all("table"):
        if table.find("thead"):
            continue
        first = table.find("tr")
        if first is None or first.find("td") or not first.find("th"):
            continue
        thead = soup.new_tag("thead")
        thead.append(first.extract())
        table.insert(0, thead)


_NESTED_PAGE_ANCHOR = re.compile(r'<a name="page(\d+)"></a>')


def _emit(blocks: List[_Block]) -> str:
    parts = []
    for blk in blocks:
        if blk.kind == "text":
            parts.append(f"<p>{blk.html}</p>")
        elif blk.kind == "quote":
            parts.append(f"<blockquote>{blk.html}</blockquote>")
        else:
            parts.append(blk.html)
    html = "\n\n".join(parts)
    # a page anchor inside a heading / figure (MTO-NWA: <center><a name=page60></a><h2>...) is a
    # page start too
    html = _NESTED_PAGE_ANCHOR.sub(r'<a id="page\1"></a>', html)
    return "<html><body>\n" + html + "\n</body></html>\n"


def _parse(html: str) -> Any:
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html5lib")
    except Exception as e:  # bs4.FeatureNotFound when html5lib is missing
        raise HyperWarParserUnavailable(
            "html5lib is required to convert HyperWar HTML (pip install -r requirements.txt): "
            f"{e}"
        ) from e


_NOTE_HREF = re.compile(r"^#(fn|cn)(\d{1,6})$", re.I)


def _canonical_notes(soup: Any) -> int:
    """Rewrite every note anchor to the ONE form src/notes.py reads. HyperWar books differ:
    XChannel ``<sup><a name=cn1 href=#fn1>1</a></sup>`` / ``[<a name=fn1 href=#cn1>1</a>]``;
    Logistics1 a definition without brackets ``<a name=fn1 href=#cn1>1</a>.``; CBI and MTO-NWA
    the sup INSIDE the link ``<a href=#fn1 name=cn1><sup>1</sup></a>`` and a bold definition number
    ``<a href=#cn1 name=fn1><b>1</b></a>.`` - three of four books parsed 0 notes. Markup only: the
    number and every word stay; the full stop after a definition number (the source's list
    delimiter, here replaced by the brackets) is dropped. Returns the anchors rewritten.
    """
    count = 0
    for a in list(soup.find_all("a", href=_NOTE_HREF)):
        m = _NOTE_HREF.match(a.get("href", ""))
        if m is None or a.get_text(strip=True) != m.group(2):
            continue  # a link to a note that is not the note's own number
        kind, number = m.group(1).lower(), m.group(2)
        own = "cn" if kind == "fn" else "fn"
        target = a.parent if getattr(a.parent, "name", None) == "sup" else a
        new = soup.new_tag("a", href=f"#{kind}{number}")
        new["name"] = f"{own}{number}"
        new.string = number
        if kind == "fn":  # an in-text marker
            sup = soup.new_tag("sup")
            sup.append(new)
            target.replace_with(sup)
        else:  # a definition
            _strip_definition_delimiters(target)
            target.replace_with(new)
            new.insert_before("[")
            new.insert_after("] ")
        count += 1
    return count


def _strip_definition_delimiters(anchor: Any) -> None:
    """Remove the source's own delimiters around a definition number - ``[`` ``]`` (XChannel) or
    a trailing ``.`` (Logistics1, CBI) - so the canonical brackets are not doubled."""
    from bs4.element import NavigableString

    before, after = anchor.previous_sibling, anchor.next_sibling
    if isinstance(before, NavigableString) and before.rstrip().endswith("["):
        before.replace_with(NavigableString(before.rstrip()[:-1]))
    if isinstance(after, NavigableString):
        text = after.lstrip()
        if text.startswith("]") or text.startswith("."):
            after.replace_with(NavigableString(text[1:].lstrip()))


def normalise_hyperwar_html(html: str) -> tuple:
    """(normalised HTML, NormaliseReport). Input without the HyperWar signature is returned
    unchanged (report.pages == 0). Raises HyperWarParserUnavailable without html5lib (never a
    silent fallback to the page-as-paragraph conversion)."""
    report = NormaliseReport()
    if not is_hyperwar_html(html):
        return html, report
    fixed = _RAW_PAGE_ANCHOR.sub(r'<a name="page\1"></a>', _BROKEN_BR.sub("<br>", html))
    report.labels = len(_LABEL_HTML.findall(html))
    soup = _parse(fixed)
    _drop_markup_only_nodes(soup)
    _expose_page_breaks(soup)
    _unwrap_inline_around_blocks(soup)
    for tag in soup.find_all("font"):
        tag.unwrap()
    for tag in soup.find_all("hr"):
        tag.decompose()  # page and table-notes rules: layout, never content
    report.notes = _canonical_notes(soup)
    _promote_headers(soup)
    body = soup.body or soup
    blocks = _Walker(report).body(body)
    _classify_quotes(blocks, report)
    blocks = _rejoin(blocks, report)
    report.paragraphs = sum(1 for b in blocks if b.kind == "text")
    return _emit(blocks), report
