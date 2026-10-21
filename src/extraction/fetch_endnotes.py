"""Fetch endnote/footnote text from ibiblio HTML pages.

Parses two patterns:
  - BreakoutAndPursuit: dedicated fn*.html pages with <a name="fnN"> anchors
  - CrossChannelAttack: footnotes at bottom of chapter pages with <a name=fnN> anchors

Caches fetched pages on disk to avoid re-downloading.
Resolves cross-references like "cited in n. 5, above".
"""

import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path("cache/endnote_pages")


class _FootnoteParser(HTMLParser):
    """Extract footnote text keyed by anchor name from ibiblio HTML."""

    def __init__(self):
        super().__init__()
        self.footnotes: Dict[int, str] = {}
        self._current_fn: Optional[int] = None
        self._buf: List[str] = []
        self._in_fn = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        name = attr_dict.get("name", "")

        # Match <a name="fn5"> or <a name=fn5>
        match = re.match(r"fn(\d+)", name)
        if match:
            # Save previous footnote
            self._save_current()
            self._current_fn = int(match.group(1))
            self._buf = []
            self._in_fn = True

    def handle_endtag(self, tag):
        # Stop collecting at next <hr> or end of content
        if tag == "hr" and self._in_fn:
            self._save_current()
            self._in_fn = False

    def handle_data(self, data):
        if self._in_fn:
            self._buf.append(data)

    def _save_current(self):
        if self._current_fn is not None and self._buf:
            text = " ".join("".join(self._buf).split()).strip()
            # Remove leading "[N]" or "N." anchor text artifacts
            text = re.sub(r"^\[?\d+\]?\s*\.?\s*", "", text)
            # Remove trailing bracket artifacts
            text = re.sub(r"\s*\[\s*$", "", text)
            if text:
                self.footnotes[self._current_fn] = text

    def close(self):
        self._save_current()
        super().close()


def _fetch_page(url: str) -> Optional[str]:
    """Fetch HTML page with caching."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Cache key from URL
    safe_name = re.sub(r"[^\w]", "_", url) + ".html"
    cache_file = CACHE_DIR / safe_name

    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    from src.utils.url_hygiene import fetch_text_safe

    html, status = fetch_text_safe(url, timeout=30)
    if html is None:
        if status not in (None, 200):
            logger.warning("Failed to fetch %s: HTTP %s", url, status)
        else:
            logger.warning("Failed to fetch %s: blocked or unreachable", url)
        return None
    cache_file.write_text(html, encoding="utf-8")
    return html


def _parse_footnotes_from_html(html: str) -> Dict[int, str]:
    """Parse all footnotes from an ibiblio HTML page."""
    parser = _FootnoteParser()
    parser.feed(html)
    parser.close()
    return parser.footnotes


def _resolve_cross_references(
    footnotes: Dict[int, str],
) -> Dict[int, str]:
    """Resolve cross-references like 'cited in n. 5', 'cited n. 5', 'see n. 5'."""
    resolved = dict(footnotes)
    # Matches: "cited in n. 5, above", "cited n. 24, above", "see n. 4", "See n. 4."
    pattern = re.compile(
        r"(?:cited(?:\s+in)?\s+n\.\s*(\d+)|[Ss]ee\s+n\.\s*(\d+))", re.IGNORECASE
    )

    for num, text in list(resolved.items()):
        for match in pattern.finditer(text):
            ref_num = int(match.group(1) or match.group(2))
            ref_text = resolved.get(ref_num)
            if ref_text and ref_num != num:
                tag = f"[see n. {ref_num}: {ref_text}]"
                resolved[num] = resolved[num].replace(match.group(0), tag, 1)

    return resolved


def _group_by_page(footnotes_meta: List[Dict]) -> Dict[str, List[int]]:
    """Group footnote numbers by their base URL page."""
    pages: Dict[str, List[int]] = {}
    for fn in footnotes_meta:
        url = fn.get("url", "")
        num = fn.get("number")
        if not url or num is None:
            continue
        base_url = url.split("#")[0]
        pages.setdefault(base_url, []).append(num)
    return pages


def _partition_footnotes(
    footnotes_meta: List[dict],
) -> tuple[Dict[int, str], List[dict]]:
    """Split parsed footnotes into (already-have-text, need-HTML-fetch)."""
    all_footnotes: Dict[int, str] = {}
    need_fetch: List[dict] = []
    for fn in footnotes_meta:
        if not isinstance(fn, dict):
            continue  # malformed entry — no number/url/text to use or fetch; skip
        num = fn.get("number")
        txt = (fn.get("text") or "").strip()
        if isinstance(num, int) and txt:
            all_footnotes[num] = txt
        else:
            need_fetch.append(fn)
    return all_footnotes, need_fetch


def _fetch_footnotes_via_html(
    need_fetch: List[dict], all_footnotes: Dict[int, str]
) -> None:
    """Fetch remaining footnotes from their source HTML pages, mutating all_footnotes."""
    pages = _group_by_page(need_fetch)
    for base_url, numbers in pages.items():
        html = _fetch_page(base_url)
        if not html:
            continue

        page_fns = _parse_footnotes_from_html(html)
        for num in numbers:
            if num in page_fns:
                all_footnotes[num] = page_fns[num]
            else:
                logger.debug("Footnote %d not found on %s", num, base_url)


def fetch_endnote_texts(
    parsed_file: Path,
) -> Dict[int, str]:
    """Fetch actual endnote/footnote text for a parsed file.

    Args:
        parsed_file: Path to *-parsed.json

    Returns:
        Dict mapping footnote number -> text content
    """
    if not parsed_file.exists():
        return {}

    data = json.loads(parsed_file.read_text(encoding="utf-8"))
    footnotes_meta = data.get("footnotes", [])
    if not footnotes_meta:
        return {}

    # Prefer citation text already captured by the parser (src/parser.extract_footnotes
    # now pairs the in-text #fn marker with its end-of-chapter #cn definition). When the
    # parsed footnotes carry `text`, use it directly and SKIP the fragile HTML re-fetch
    # (no network/NAT dependency, no link-rot). Only numbers whose parsed text is empty
    # fall back to the HTML fetch below (legacy parses that predate the text capture).
    all_footnotes, need_fetch = _partition_footnotes(footnotes_meta)

    _fetch_footnotes_via_html(need_fetch, all_footnotes)

    if all_footnotes:
        all_footnotes = _resolve_cross_references(all_footnotes)
        logger.info(
            "Fetched %d/%d endnote texts for %s (%d from parsed text, %d via HTML)",
            len(all_footnotes),
            len(footnotes_meta),
            parsed_file.name,
            len(footnotes_meta) - len(need_fetch),
            len(need_fetch),
        )

    return all_footnotes


def format_endnote_text_block(
    endnote_texts: Dict[int, str],
    ref_numbers: List[int],
) -> str:
    """Format fetched endnote texts for inclusion in a prompt.

    Args:
        endnote_texts: All fetched texts for the chapter
        ref_numbers: Specific reference numbers for this sub-event

    Returns:
        Formatted text block, or empty string if no texts available
    """
    lines = []
    for num in sorted(ref_numbers):
        text = endnote_texts.get(num)
        if text:
            lines.append(f"  {num}. {text}")

    if not lines:
        return ""

    return "Actual endnote/footnote text (from source document):\n" + "\n".join(lines)
