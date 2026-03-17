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

import requests

from src.utils.http_pool import get_session

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

    try:
        session = get_session()
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        html = resp.text
        cache_file.write_text(html, encoding="utf-8")
        return html
    except requests.RequestException as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def _parse_footnotes_from_html(html: str) -> Dict[int, str]:
    """Parse all footnotes from an ibiblio HTML page."""
    parser = _FootnoteParser()
    parser.feed(html)
    parser.close()
    return parser.footnotes


def _resolve_cross_references(
    footnotes: Dict[int, str],
) -> Dict[int, str]:
    """Resolve cross-references like 'cited in n. 5, above'."""
    resolved = dict(footnotes)
    pattern = re.compile(r"cited in n\.\s*(\d+),?\s*above", re.IGNORECASE)

    for num, text in list(resolved.items()):
        match = pattern.search(text)
        if match:
            ref_num = int(match.group(1))
            ref_text = resolved.get(ref_num)
            if ref_text:
                resolved[num] = pattern.sub(f"[see n. {ref_num}: {ref_text}]", text)

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

    pages = _group_by_page(footnotes_meta)
    all_footnotes: Dict[int, str] = {}

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

    if all_footnotes:
        all_footnotes = _resolve_cross_references(all_footnotes)
        logger.info(
            "Fetched %d/%d endnote texts for %s",
            len(all_footnotes),
            len(footnotes_meta),
            parsed_file.name,
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
