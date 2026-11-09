"""Phase 0: correct an OCR'd book's chapters against its transcription (the validation URL).

Only a document uploaded WITH a validation URL is cross-checked; one without is never checked
against nothing (owner 2026-11-08). The URL is either the transcription of one document (its
whole text) or a book's contents page (HyperWar ``index.html``) whose links lead to each
chapter's transcription. Each published chapter (src/ingestion/ocr_chapters) is matched to the
transcription page whose link title is its own title, cross-checked
(src/ingestion/pdf_html_crosscheck, Chandra's reading only - every PDF goes through OCR) and
corrected (src/ingestion/pdf_text_corrections: only the OCR artefacts the book itself settles,
only to readings the newer, PDF edition prints), then proved by a second cross-check. A chapter
whose transcription is missing, does not align, or whose corrections do not land is left as the
OCR read it, and says so in the log.

The fetch is SSRF-guarded (src/utils/url_hygiene.safe_get: public hosts only, no redirects,
bounded body); chapter links are followed only within the contents page's own directory.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from src.ingestion import pdf_html_crosscheck as X
from src.ingestion import pdf_text_corrections as C
from src.ingestion.chunk_pages import PAGE_SEPARATOR_RE
from src.ingestion.ocr_chapters import Section

_ANCHOR = re.compile(r'<a id="page(\d+)"></a>')
_LINK = re.compile(
    r"<a\b[^>]{0,300}?href\s*=\s*[\"']?([^\"'\s>]{1,300})[\"']?[^>]{0,300}>(.{1,300}?)</a>",
    re.I | re.S,
)
_TAG = re.compile(r"<[^>]+>")
MIN_ALIGNED = (
    0.9  # a chapter aligning less is the wrong page (or missing text): not corrected
)
_MIN_GUARDED_WORDS = 50
_MIN_NARRATIVE_SHARE = (
    0.5  # a transcription holding less than this of the PDF's words is not it
)
_MIN_JUDGED_WORDS = 100  # ... judged only for a chapter of at least this many words
_MAX_REVIEW = (
    300  # review items kept per chapter in the log (the total is always counted)
)
_REVIEW_CONTEXT = 200  # characters of context kept per item
_MIN_TITLE_MATCH = 0.75
Fetch = Callable[[str], Optional[str]]


@dataclass
class ChapterCheck:
    number: int
    label: str
    url: str = ""
    kind: str = "chapter"  # the section's kind: chapter | appendix | matter
    # corrected | verified_no_change | no_transcription | not_aligned | not_landed | no_pages
    # | error
    status: str = ""
    error: str = ""
    # Differences neither rule settles (a name the book never prints the other way, a number, two
    # valid words): the page must be looked at. The PDF's OCR stands; this is the list for a human.
    review: List[dict] = field(default_factory=list)
    review_total: int = 0
    streams: List[dict] = field(default_factory=list)
    corrections: dict = field(default_factory=dict)


@dataclass
class BookCheck:
    validation_url: str
    chapters: List[ChapterCheck] = field(default_factory=list)

    def summary(self) -> dict:
        status = Counter(c.status for c in self.chapters)
        applied = sum(c.corrections.get("applied", 0) for c in self.chapters)
        return {
            "validation_url": self.validation_url,
            "status": dict(status),
            "applied": applied,
            "review": sum(c.review_total for c in self.chapters),
        }

    def unchecked_chapters(self) -> List[ChapterCheck]:
        """Chapters (not appendices: a book's appendices often have no transcription) left
        uncorrected for a reason that needs a person: no page, pages the cross-check cannot
        read (out of sequence), not the chapter, not landed, an error."""
        bad = ("no_transcription", "no_pages", "not_aligned", "not_landed", "error")
        return [c for c in self.chapters if c.kind == "chapter" and c.status in bad]

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return {
            "summary": self.summary(),
            "chapters": [asdict(c) for c in self.chapters],
        }


def fetch_html(url: str) -> Optional[str]:
    """The page at ``url`` (SSRF-guarded), or None."""
    from src.utils.url_hygiene import safe_get

    resp = safe_get(url)
    if resp is None or resp.status_code != 200:
        return None
    if "charset" not in resp.headers.get("content-type", "").lower():
        # requests assumes ISO-8859-1 for text/* without a charset: "Saint-Lô" would read wrongly
        resp.encoding = (
            _declared_charset(resp.content) or resp.apparent_encoding or "utf-8"
        )
    return resp.text


_META_CHARSET = re.compile(rb"<meta[^>]{0,200}?charset\s*=\s*[\"']?([\w-]{2,30})", re.I)


def _declared_charset(content: bytes) -> str:
    """The charset the page itself declares (``<meta charset>`` / ``http-equiv``), if any."""
    m = _META_CHARSET.search(content[:2048] or b"")
    if not m:
        return ""
    name = m.group(1).decode("ascii", "ignore")
    try:
        import codecs

        codecs.lookup(name)
    except LookupError:
        return ""
    return name


def transcription_pages(
    url: str, sections: List[Section], fetch: Fetch = fetch_html
) -> Dict[int, str]:
    """{published chapter number: transcription URL}. A contents page's links are matched to
    the chapters by title (one link per chapter, the best match above a threshold); a page with
    no matching links is the transcription of a one-chapter document."""
    page = fetch(url)
    published = [s for s in sections if s.published]
    if page is None or not published:
        return {}
    links = _chapter_links(page, url)
    found = _assign_links(published, links)
    if not found and len(published) == 1:
        found[published[0].number] = url
    return found


def _assign_links(
    published: List[Section], links: List[Tuple[str, str]]
) -> Dict[int, str]:
    """One link per chapter, assigned best score first across ALL chapters (an exact title
    beats a title that merely starts the same), each link to one chapter at most."""
    scored = []
    for s in published:
        want = _title_key(s.title or s.label)
        for url, title in links:
            score = _title_score(want, _title_key(title)) if want else 0.0
            if score >= _MIN_TITLE_MATCH:
                scored.append(
                    (-score, abs(len(want) - len(_title_key(title))), s.number, url)
                )
    found: Dict[int, str] = {}
    taken: set = set()
    for _neg, _gap, number, url in sorted(scored):
        if number not in found and url not in taken:
            found[number] = url
            taken.add(url)
    return found


def _chapter_links(page: str, base: str) -> List[Tuple[str, str]]:
    """(absolute URL, link title) of the page's links to other pages in its own directory."""
    folder = base.rsplit("/", 1)[0] + "/"
    out = []
    for href, text in _LINK.findall(page):
        if "#" in href:
            continue
        target = urljoin(base, href)
        if not target.startswith(folder) or urlparse(target).path.count(
            "/"
        ) != urlparse(base).path.count("/"):
            continue
        title = re.sub(r"\s+", " ", _TAG.sub(" ", text)).strip()
        if title:
            out.append((target, title))
    return out


def _title_score(a: str, b: str) -> float:
    """Similarity of two title keys. One title opening the other (a subtitle the contents page
    leaves out: "DIVISIONS AVAILABLE TO GERMANY ON 6 JUNE 1944 (BY TYPE)") scores 0.9 when the
    shorter has three or more words - below an exact match (1.0)."""
    if a == b:
        return 1.0
    short, long_ = sorted((a, b), key=len)
    if len(short.split()) >= 3 and long_.startswith(short + " "):
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def _title_key(title: str) -> str:
    """Upper case, letters and digits only, typographic dashes as hyphens: "Outline Overlord
    (January–July 1943)" ~ "OUTLINE OVERLORD (JANUARY-JULY 1943)"."""
    t = title.replace("–", "-").replace("—", "-").upper()
    return " ".join(re.findall(r"[A-Z0-9]+", t))


def to_chandra(section_text: str) -> Optional[Tuple[int, str, List[int]]]:
    """(first PDF page, the section as Chandra paginated markdown, its pages) - the form the
    cross-check reads - or None when the section has no page anchors."""
    pieces = _ANCHOR.split(section_text)
    if len(pieces) < 3:
        return None
    pages = [int(p) for p in pieces[1::2]]
    bodies = pieces[2::2]
    if pieces[0].strip() or pages != list(range(pages[0], pages[0] + len(pages))):
        return None  # text before the first anchor, or pages out of sequence: not reversible
    out = bodies[0]
    for i, body in enumerate(bodies[1:], start=1):
        out += f"\n{i}{'-' * 48}\n{body}"
    return pages[0], out, pages


def from_chandra(markdown: str, pages: List[int]) -> str:
    """The inverse of ``to_chandra``."""
    bodies = PAGE_SEPARATOR_RE.split(markdown)[::2]
    if len(bodies) != len(pages):
        raise ValueError(f"page count changed: {len(bodies)} != {len(pages)}")
    out = ""
    for n, (page, body) in enumerate(zip(pages, bodies)):
        body = body[1:] if n and body.startswith("\n") else body
        body = body[:-1] if n + 1 < len(bodies) and body.endswith("\n") else body
        out += f'<a id="page{page}"></a>{body}'
    return out


def correct_sections(
    sections: List[Section], validation_url: str, fetch: Fetch = fetch_html
) -> BookCheck:
    """Cross-check and correct each published section against its transcription, IN PLACE
    (``section.text``). Returns the book's log."""
    book = BookCheck(validation_url)
    urls = transcription_pages(validation_url, sections, fetch)
    vocab = _book_vocabulary(sections)
    for s in (s for s in sections if s.published):
        check = ChapterCheck(s.number, s.label, urls.get(s.number, ""), s.kind)
        book.chapters.append(check)
        try:
            _correct_section(s, check, vocab, fetch)
        except Exception as e:  # noqa: BLE001 - one chapter must not stop the others
            check.status, check.error = "error", f"{type(e).__name__}: {str(e)[:200]}"
    return book


def _correct_section(
    s: Section, check: ChapterCheck, vocab: Counter, fetch: Fetch
) -> None:
    from src.ingestion.hyperwar_html import normalise_hyperwar_html

    chandra = to_chandra(s.text)
    html = fetch(check.url) if check.url else None
    if chandra is None or html is None:
        check.status = "no_pages" if chandra is None else "no_transcription"
        return
    first, markdown, pages = chandra
    streams = X.html_streams(normalise_hyperwar_html(html)[0])
    checks = C.stream_checks(
        streams, X.chandra_tokens(markdown, first, None, streams), vocab
    )
    X.reconcile_moved(checks)
    check.streams = [X.summary(c) for c in checks]
    if not _aligned(check.streams):
        check.status = "not_aligned"
        return
    _record_review(check, checks)
    verified = C.correct_verified(markdown, first, streams, checks, vocab)
    check.corrections = verified.log()
    if not verified.ok:
        check.status = "not_landed"
        return
    s.text = from_chandra(verified.corrected, pages)
    check.status = "corrected" if verified.plan.corrections else "verified_no_change"


def _record_review(check: ChapterCheck, checks: list) -> None:
    """List the chapter's ``review`` differences for a person: stream, PDF and printed page, both
    readings and a little context. Nothing is changed for them - the PDF's OCR reading stands
    (the PDF is the source of record; HyperWar is the 1951 printing)."""
    items = [
        {
            "stream": c.stream,
            "pdf_page": d.pdf_page,
            "printed_page": d.printed_page,
            "pdf": d.pdf,
            "html": d.html,
            "context": " ".join(d.context.split())[:_REVIEW_CONTEXT],
        }
        for c in checks
        for d in c.differences
        if d.cls == "review"
    ]
    check.review_total = len(items)
    check.review = items[:_MAX_REVIEW]


def _aligned(streams: List[dict]) -> bool:
    """The transcription is the chapter: its words align with the PDF's, and (narrative) it
    holds most of them - a small page that aligns perfectly is not the whole chapter."""
    for st in streams:
        if st["html_words"] >= _MIN_GUARDED_WORDS and st["aligned_share"] < MIN_ALIGNED:
            return False
        if (
            st["stream"] == "narrative"
            and st["pdf_words"] >= _MIN_JUDGED_WORDS
            and st["html_words"] < _MIN_NARRATIVE_SHARE * st["pdf_words"]
        ):
            return False
    return True


def _book_vocabulary(sections: List[Section]) -> Counter:
    """The whole book's words as Chandra read them (no text layer: every PDF goes through OCR)."""
    vocab: Counter = Counter()
    for s in sections:
        chandra = to_chandra(s.text)
        if chandra:
            vocab.update(X.token_vocabulary(X.chandra_tokens(chandra[1], chandra[0])))
    return vocab
