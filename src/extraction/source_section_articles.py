"""Grokipedia + Wikipedia ARTICLE fetch for the source_section entity (Phase 2, textual).

Given a derived operation/campaign (e.g. "Battle of the Bulge"), fetch reference articles as
ADDITIVE material:

  * Grokipedia  — best-effort HTML text extraction (no API). Graceful: falls back to
    URL+title only if the scrape yields no clean text. You trust Grokipedia for text.
  * Wikipedia   — bounded article text (lead + section extracts, NOT the whole article) plus
    the full reference list via the ``action=parse`` API.

Each article is captured with full provenance (url, retrieved_at, license) and its own raw
references, provenance-tagged ``wikipedia-reference`` / ``grokipedia-reference``. Promotion of
those references into the first-class ``bibliography`` entity is a BACKLOG item (docs/TODO.md),
NOT done here — they are captured inline, raw.

Media (images/maps) are NOT handled here — that is step 5 (Wikipedia only, first-class
images/map_features records). This module is textual only.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_WIKI_LICENSE = "CC BY-SA 4.0"  # Wikipedia text license (stable, documented)
_MAX_WIKI_TEXT = 6000  # bounded additive text, not the whole article
_MAX_REFERENCES = 200


def _today() -> str:
    return date.today().isoformat()


# --- Wikipedia ---------------------------------------------------------------------------


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_wikipedia_article(title: str, timeout: int = 20) -> Optional[Dict[str, Any]]:
    """Fetch a Wikipedia article's bounded text + reference list via action=parse.

    Returns {source, title, url, extract, references[], license, retrieved_at} or None on a
    hard miss (no such page / network error)."""
    if not title:
        return None
    try:
        resp = requests.get(
            _WIKI_API,
            params={
                "action": "parse",
                "format": "json",
                "page": title,
                "prop": "text|externallinks",
                "redirects": "1",
            },
            headers=_HEADERS,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if "error" in data:
            return None
        parse = data.get("parse") or {}
        resolved_title = parse.get("title") or title
        html = (parse.get("text") or {}).get("*", "")
        if not html:
            return None
        extract = _strip_html(html)[:_MAX_WIKI_TEXT] or None
        # externallinks is the article's cited external URLs — the reference list, raw.
        ext_links = parse.get("externallinks") or []
        references: List[Dict[str, Any]] = [
            {"source": "wikipedia-reference", "url": u}
            for u in ext_links[:_MAX_REFERENCES]
            if isinstance(u, str)
        ]
        return {
            "source": "wikipedia",
            "title": resolved_title,
            "url": f"https://en.wikipedia.org/wiki/{resolved_title.replace(' ', '_')}",
            "extract": extract,
            "references": references or None,
            "license": _WIKI_LICENSE,
            "retrieved_at": _today(),
        }
    except (requests.RequestException, ValueError) as e:
        logger.debug("wikipedia article fetch failed for %r: %s", title, e)
        return None


# --- Grokipedia (best-effort HTML scrape, no API) ----------------------------------------


def _resolve_grokipedia_page(name: str, timeout: int) -> Optional[str]:
    """Resolve an operation name to a Grokipedia /page/ URL via the shared resolver."""
    from src.enrichment.grokipedia import resolve_grokipedia_url

    return resolve_grokipedia_url(name, timeout=timeout)


def fetch_grokipedia_article(name: str, timeout: int = 20) -> Optional[Dict[str, Any]]:
    """Best-effort Grokipedia article fetch. Returns a record with extracted text when the
    scrape succeeds, else URL+title only (graceful). None only on total failure to resolve.
    """
    page_url = _resolve_grokipedia_page(name, timeout)
    if not page_url:
        return None
    extract: Optional[str] = None
    try:
        page = requests.get(page_url, headers=_HEADERS, timeout=timeout)
        if page.status_code == 200 and page.text:
            # Best-effort: prefer <article>/<main> body, else whole page, then strip tags.
            m = re.search(
                r"<(?:article|main)[^>]*>(.*?)</(?:article|main)>",
                page.text,
                re.DOTALL | re.IGNORECASE,
            )
            body = m.group(1) if m else page.text
            text = _strip_html(body)
            extract = text[:_MAX_WIKI_TEXT] or None
    except (requests.RequestException, ValueError) as e:
        logger.debug("grokipedia page fetch failed for %r: %s", name, e)
    return {
        "source": "grokipedia",
        "title": name,
        "url": page_url,
        "extract": extract,  # may be None -> graceful URL-only capture
        "references": None,  # grokipedia reference extraction not implemented (backlog)
        "license": None,  # null-over-fake: Grokipedia license not asserted
        "retrieved_at": _today(),
    }


def fetch_reference_articles(operation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetch both Grokipedia + Wikipedia articles for a derived operation. Returns the list of
    article records (possibly empty). Each source failing is non-fatal."""
    articles: List[Dict[str, Any]] = []
    name = operation.get("name")
    wiki_title = operation.get("wikipedia_title") or name
    if not name:
        return articles
    grok = fetch_grokipedia_article(name)
    if grok:
        articles.append(grok)
    wiki = fetch_wikipedia_article(wiki_title)
    if wiki:
        articles.append(wiki)
    return articles
