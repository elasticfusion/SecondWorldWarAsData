"""Search for supplemental material URLs using multiple sources."""

import logging
from typing import Dict, Optional, Tuple
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


def search_gutenberg_openserp(
    title: str,
    author: Optional[str] = None,
    openserp_url: str = "http://localhost:7001",
) -> Optional[str]:
    """
    Search Project Gutenberg using OpenSERP.

    Returns:
        URL if found, None otherwise
    """
    try:
        from src.utils.search_query_loader import render_search_queries

        queries = render_search_queries(
            "bibliography", "gutenberg", title=title, author=author or ""
        )
        query = (
            queries[0]
            if queries
            else f"{title} {author or ''} site:gutenberg.org".strip()
        )

        response = requests.post(
            f"{openserp_url}/search",
            json={"query": query, "engines": ["google"]},
            timeout=30.0,
        )

        if response.status_code == 200:
            results = response.json().get("results", [])
            for result in results:
                url = result.get("url", "")
                if "gutenberg.org" in url:
                    logger.info("Found on Gutenberg: %s", url)
                    return url

    except Exception as e:
        logger.debug("Gutenberg search failed: %s", e)

    return None


def search_archive_org(
    title: str, author: Optional[str] = None, periodical: Optional[str] = None
) -> Optional[str]:
    """
    Search archive.org using advanced search API.

    Returns:
        URL if found, None otherwise
    """
    # Guard: skip titles that are too short, numeric, or clearly primary source citations
    if not title or len(title) < 10 or title.strip().isdigit():
        return None
    if any(
        kw in title.lower()
        for kw in [
            "memo,",
            "ltr,",
            "msg,",
            "jnl,",
            "aar,",
            "tel conv",
            "file ",
            "telecon,",
            "sitrep",
            "rpt,",
            "instrs,",
        ]
    ):
        return None

    from src.utils.search_cache import cache_result, get_cached

    query_key = f"{title}|{author or ''}|{periodical or ''}"
    cached = get_cached("archive_org", query_key)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    result = _search_archive_org_api(title, author, periodical)
    cache_result("archive_org", query_key, result)
    return result


def _search_archive_org_api(
    title: str, author: Optional[str] = None, periodical: Optional[str] = None
) -> Optional[str]:
    """Execute archive.org API search."""
    try:
        query = _build_archive_query(title, author, periodical)
        # Search API
        url = f"https://archive.org/advancedsearch.php?q={quote(query)}&fl[]=identifier&fl[]=title&output=json&rows=5"

        response = requests.get(url, timeout=30.0, allow_redirects=True)

        if response.status_code == 200:
            data = response.json()
            docs = data.get("response", {}).get("docs", [])

            if docs:
                identifier = docs[0].get("identifier")
                if identifier:
                    result_url = f"https://archive.org/details/{identifier}"
                    logger.info("Found on Archive.org: %s", result_url)
                    return result_url

    except Exception as e:
        logger.debug("Archive.org search failed: %s", e)

    return None


def search_archive_org_candidates(
    title: str,
    author: Optional[str] = None,
    periodical: Optional[str] = None,
    rows: int = 5,
) -> "list[Tuple[str, str]]":
    """Return UP TO ``rows`` archive.org candidates as ``(url, title)`` pairs for the caller
    to iterate + verify (the NARA-style multi-finding pattern), instead of blindly taking the
    first hit. Applies the same citation-shape guards as ``search_archive_org``. Never raises.
    """
    if not title or len(title) < 10 or title.strip().isdigit():
        return []
    if any(
        kw in title.lower()
        for kw in [
            "memo,",
            "ltr,",
            "msg,",
            "jnl,",
            "aar,",
            "tel conv",
            "file ",
            "telecon,",
            "sitrep",
            "rpt,",
            "instrs,",
        ]
    ):
        return []
    out: "list[Tuple[str, str]]" = []
    try:
        query = _build_archive_query(title, author, periodical)
        url = (
            f"https://archive.org/advancedsearch.php?q={quote(query)}"
            f"&fl[]=identifier&fl[]=title&output=json&rows={rows}"
        )
        response = requests.get(url, timeout=30.0, allow_redirects=True)
        if response.status_code == 200:
            docs = response.json().get("response", {}).get("docs", [])
            for doc in docs:
                identifier = doc.get("identifier")
                if not identifier:
                    continue
                cand_title = doc.get("title") or ""
                if isinstance(cand_title, list):  # archive.org may return a list
                    cand_title = cand_title[0] if cand_title else ""
                out.append(
                    (f"https://archive.org/details/{identifier}", str(cand_title))
                )
    except Exception as e:
        logger.debug("Archive.org candidate search failed: %s", e)
    return out


def search_gutenberg_candidates(
    title: str,
    author: Optional[str] = None,
    openserp_url: str = "http://localhost:7001",  # kept for signature compat; unused
) -> "list[Tuple[str, str]]":
    """Return Project Gutenberg candidates as ``(url, title)`` pairs for iterate + verify, via
    the NATIVE Gutendex API (https://gutendex.com) — NOT OpenSERP. Gutenberg is an independent
    source and must not depend on the OpenSERP service being enabled/running. Mirrors
    ``search_archive_org_candidates`` so the resolver treats every source uniformly. Low hit
    expectation (out-of-copyright literary texts, not WWII operational records) but cheap and
    self-contained. Never raises."""
    out: "list[Tuple[str, str]]" = []
    if not title or len(title) < 4:
        return out
    try:
        search_term = title if not author else f"{title} {author}"
        response = requests.get(
            "https://gutendex.com/books",
            params={"search": search_term},
            timeout=30.0,
        )
        if response.status_code == 200:
            for book in response.json().get("results", [])[:5]:
                gid = book.get("id")
                btitle = book.get("title") or ""
                # Prefer the canonical ebook page; fall back to any text/html format URL.
                url = f"https://www.gutenberg.org/ebooks/{gid}" if gid else ""
                if not url:
                    fmts = book.get("formats") or {}
                    url = next(
                        (u for k, u in fmts.items() if "text/html" in k),
                        next(iter(fmts.values()), ""),
                    )
                if url:
                    out.append((url, btitle))
    except Exception as e:
        logger.debug("Gutenberg (gutendex) candidate search failed: %s", e)
    return out


_CORPORATE_AUTHOR_KW = (
    "department",
    "division",
    "admiralty",
    "office",
    "command",
    "staff",
)


def _author_search_term(author: str) -> str:
    """archive.org creator: term — full string for corporate authors, last name for
    personal names ('LastName, First' or 'First LastName')."""
    if any(kw in author.lower() for kw in _CORPORATE_AUTHOR_KW):
        return author
    if "," in author:
        return author.split(",")[0].strip()
    parts = author.split()
    return parts[-1] if len(parts[-1]) > 2 else parts[0]


def _build_archive_query(
    title: str, author: Optional[str], periodical: Optional[str]
) -> str:
    """Build the archive.org advancedsearch query. Parenthesized keywords (not exact
    quotes) because Archive.org metadata titles often differ from citation titles."""
    query_parts = []
    if title:
        words = " ".join(w for w in title.split() if len(w) > 2)
        query_parts.append(f"title:({words})")
    if author:
        query_parts.append(f"creator:({_author_search_term(author)})")
    if periodical:
        query_parts.append(f'"{periodical}"')
    query = " AND ".join(query_parts) if query_parts else title
    return query + " AND mediatype:texts"


def fetch_archive_org_metadata(identifier: str) -> Optional[Dict]:
    """Fetch metadata and PDF link from Archive.org for a given identifier."""
    try:
        resp = requests.get(f"https://archive.org/metadata/{identifier}", timeout=30.0)
        if resp.status_code != 200:
            return None

        data = resp.json()
        meta = data.get("metadata", {})
        files = data.get("files", [])

        pdf_url = _select_archive_pdf(identifier, files)
        text_formats = _archive_text_formats(identifier, files)

        result = {
            "archive_org_identifier": identifier,
            "archive_org_title": meta.get("title"),
            "archive_org_creator": meta.get("creator"),
            "archive_org_date": meta.get("date"),
            "archive_org_language": meta.get("language"),
            "archive_org_ocr": meta.get("ocr"),
            "archive_org_pages": meta.get("pages") or meta.get("imagecount"),
        }
        if pdf_url:
            result["pdf_url"] = pdf_url
        # Record ALL available processing formats (nothing discarded — the human/
        # retrieval can pick EPUB even if large). ``text_url``/``text_format`` is
        # only the size-aware DEFAULT recommendation, not an exclusive choice.
        if text_formats.get("epub_url"):
            result["epub_url"] = text_formats["epub_url"]
            result["epub_size"] = text_formats.get("epub_size")
        if text_formats.get("djvu_txt_url"):
            result["djvu_txt_url"] = text_formats["djvu_txt_url"]
        if text_formats.get("text_url"):
            result["text_url"] = text_formats["text_url"]
            result["text_format"] = text_formats["text_format"]

        # Remove None values
        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        logger.debug("Archive.org metadata fetch failed for %s: %s", identifier, e)
        return None


def _dl_url(identifier: str, name: str) -> str:
    return f"https://archive.org/download/{identifier}/{name}"


def _select_archive_pdf(identifier: str, files: list) -> Optional[str]:
    """Pick the best PDF: prefer source=original over derivatives (_bw/_text),
    then by size (largest = fullest scan). Deterministic, not file-list order."""
    pdfs = [f for f in files if f.get("name", "").lower().endswith(".pdf")]
    if not pdfs:
        return None

    def _rank(f: dict) -> tuple:
        original = 1 if f.get("source", "").lower() == "original" else 0
        try:
            size = int(f.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        return (original, size)

    best = max(pdfs, key=_rank)
    return _dl_url(identifier, best["name"])


# Size above which an EPUB is *flagged* as large (usually because it carries
# embedded images — photos/maps we WANT). Not a rejection threshold: retrieval is
# human-gated, so a reviewer decides whether to pull a large image-rich EPUB.
_LARGE_EPUB_BYTES = 50 * 1024 * 1024


def _file_size(f: dict) -> int:
    try:
        return int(f.get("size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _archive_text_formats(identifier: str, files: list) -> Dict:
    """Record ALL available processing formats (nothing discarded) + a default.

    EPUB is preferred as the default when present: pandoc converts it to markdown
    WITH its embedded images (photos/maps), which the raw DjVuTXT lacks entirely.
    DjVuTXT is recorded as the lightweight text-only alternative. A large EPUB is
    flagged (``epub_large``) — not rejected — since size usually means embedded
    images we want, and retrieval is human-gated anyway.
    """
    out: Dict = {}

    epubs = [f for f in files if f.get("name", "").lower().endswith(".epub")]
    epub = max(epubs, key=_file_size) if epubs else None  # richest (most images)
    if epub:
        out["epub_url"] = _dl_url(identifier, epub["name"])
        out["epub_size"] = _file_size(epub)

    txts = [
        f
        for f in files
        if f.get("name", "").lower().endswith("_djvu.txt")
        or f.get("format", "") == "DjVuTXT"
    ]
    if txts:
        out["djvu_txt_url"] = _dl_url(identifier, txts[0]["name"])

    _set_default_text(out, epub)
    return out


def _set_default_text(out: Dict, epub: Optional[dict]) -> None:
    """Set the default processing text: EPUB (with images) if present, else txt."""
    if epub:
        out["text_url"] = out["epub_url"]
        out["text_format"] = (
            "epub_large" if _file_size(epub) > _LARGE_EPUB_BYTES else "epub"
        )
    elif out.get("djvu_txt_url"):
        out["text_url"] = out["djvu_txt_url"]
        out["text_format"] = "djvu_txt"


def search_openserp(
    title: str,
    author: Optional[str] = None,
    openserp_url: str = "http://localhost:7001",
) -> Optional[str]:
    """
    General search using OpenSERP.

    Returns:
        URL if found, None otherwise
    """
    try:
        query = f'"{title}" {author or ""}'.strip()

        response = requests.post(
            f"{openserp_url}/search",
            json={"query": query, "engines": ["google", "bing"]},
            timeout=30.0,
        )

        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                url = results[0].get("url")
                logger.info("Found via OpenSERP: %s", url)
                return url

    except Exception as e:
        logger.debug("OpenSERP search failed: %s", e)

    return None


def search_llm(
    title: str, author: Optional[str] = None, grok_client=None
) -> Optional[str]:
    """
    Search using LLM knowledge.

    Returns:
        URL if found, None otherwise
    """
    if not grok_client:
        return None

    try:
        from src.utils.prompt_loader import render_prompt

        prompt = render_prompt(
            "publication_search", title=title, author=author or "", doc_type="unknown"
        )

        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You are a research librarian. Only provide URLs you are certain about.",
            temperature=0.0,
            use_cache=True,
            cache_type="supplemental_search",
        )

        url = response.strip()
        if url and url != "NOT_FOUND" and url.startswith("http"):
            logger.info("Found via LLM: %s", url)
            return url

    except Exception as e:
        logger.debug("LLM search failed: %s", e)

    return None


def sequential_search(
    title: str,
    author: Optional[str] = None,
    periodical: Optional[str] = None,
    grok_client=None,
    openserp_url: str = "http://localhost:7001",
    search_gutenberg: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Sequential search: Gutenberg → LLM → Archive.org → OpenSERP.

    Returns:
        tuple: (url, source) where source is the search method that found it
    """
    # First: Gutenberg (for books/periodicals only)
    if search_gutenberg:
        url = search_gutenberg_openserp(title, author, openserp_url)
        if url:
            return (url, "gutenberg")

    # Second: LLM search
    url = search_llm(title, author, grok_client)
    if url:
        return (url, "llm")

    # Third: Archive.org
    url = search_archive_org(title, author, periodical)
    if url:
        return (url, "archive_org")

    # Fourth: OpenSERP general search
    url = search_openserp(title, author, openserp_url)
    if url:
        return (url, "openserp")

    return (None, None)
