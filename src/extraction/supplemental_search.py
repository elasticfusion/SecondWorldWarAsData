"""Search for supplemental material URLs using multiple sources."""

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import requests

from src.utils.search_cache import cached_answer

logger = logging.getLogger(__name__)


def _answered(service: str, op: str, response: Any) -> bool:
    """True when ``service`` answered (HTTP 200, or a definite non-outage status such as 404).
    403 (blocked) / 408 / 429 / 5xx - and any non-200 from OpenSERP, our own service - is an
    outage: reported through
    service_outage (task 1.2f), so the citation it was for is retried next run instead of being
    recorded as not found online."""
    from src.utils import service_outage

    code = getattr(response, "status_code", 0)
    if code == 200:
        service_outage.ok(service)
        return True
    if service == "openserp" or code in (403, 408, 429) or code >= 500 or not code:
        service_outage.failed(service, op, f"http {code}")
        return False
    return True  # e.g. 404: a definite answer


def _unreachable(service: str, op: str, e: Exception) -> None:
    """``service`` could not be reached at all (timeout, DNS, connection): an outage."""
    from src.utils import service_outage

    service_outage.failed(service, op, type(e).__name__)


def search_gutenberg_openserp(
    title: str,
    author: Optional[str] = None,
    openserp_url: str = "http://localhost:7001",
) -> Optional[str]:
    """
    Search Project Gutenberg using OpenSERP.

    Returns:
        URL if found, None otherwise (asked once per window, a miss included)
    """
    from src.utils.search_cache import scraped_ttl_days

    return cached_answer(
        "gutenberg_openserp",
        f"{title}|{author or ''}",
        lambda: _gutenberg_via_openserp(title, author, openserp_url),
        ttl_days=scraped_ttl_days(),  # a scraped engine: empty may mean throttled, not "none"
    )


def _gutenberg_via_openserp(
    title: str, author: Optional[str], openserp_url: str
) -> Optional[str]:
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

        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        response = recorded_fetch(
            "openserp",
            f"{openserp_url}/search",
            kind=TEXTUAL,
            method="POST",
            json={"query": query, "engines": ["google"]},
            timeout=30.0,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )

        if _answered("openserp", "gutenberg_search", response):
            results = response.json().get("results", [])
            for result in results:
                url = result.get("url", "")
                if "gutenberg.org" in url:
                    logger.info("Found on Gutenberg: %s", url)
                    return url

    except Exception as e:
        _unreachable("openserp", "gutenberg_search", e)

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

    from src.utils.search_cache import get_cached

    query_key = f"{title}|{author or ''}|{periodical or ''}"
    cached = get_cached("archive_org", query_key)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    return _search_and_cache(query_key, title, author, periodical)


def _search_and_cache(
    query_key: str, title: str, author: Optional[str], periodical: Optional[str]
) -> Optional[str]:
    """Ask archive.org and cache the answer; an outage (TRANSIENT) is not cached and reads as
    no result for now (task 1.2f)."""
    from src.utils.search_cache import TRANSIENT, answer_ttl_days, cache_result

    result = _search_archive_org_api(title, author, periodical)
    # TRANSIENT is never cached; a real "nothing found" is kept for the whole answer window
    cache_result("archive_org", query_key, result, negative_days=answer_ttl_days())
    return None if result is TRANSIENT else result


def _search_archive_org_api(
    title: str, author: Optional[str] = None, periodical: Optional[str] = None
) -> Any:
    """Execute archive.org API search: the item URL, None (a genuine miss), or
    ``search_cache.TRANSIENT`` when archive.org did not answer (error / non-200): an outage must
    not be cached as "not on archive.org" for 7 days (task 1.2f)."""
    from src.utils.search_cache import TRANSIENT

    try:
        query = _build_archive_query(title, author, periodical)
        # Search API
        url = f"https://archive.org/advancedsearch.php?q={quote(query)}&fl[]=identifier&fl[]=title&output=json&rows=5"

        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        response = recorded_fetch(
            "archive-solr",
            url,
            kind=TEXTUAL,
            method="GET",
            timeout=30.0,
            allow_redirects=True,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )

        if response.status_code == 200:
            _answered("archive_org", "search", response)
            data = response.json()
            docs = data.get("response", {}).get("docs", [])

            if docs:
                identifier = docs[0].get("identifier")
                if identifier:
                    result_url = f"https://archive.org/details/{identifier}"
                    logger.info("Found on Archive.org: %s", result_url)
                    return result_url

            return None  # answered, nothing found: a real miss
        _answered("archive_org", "search", response)  # not 200: reported, not cached
        return TRANSIENT
    except Exception as e:
        _unreachable("archive_org", "search", e)  # not cached, retried next run
        return TRANSIENT


_ARCHIVE_TITLE_REJECT_KW = [
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


def _archive_title_rejected(title: str) -> bool:
    """True if the title matches a citation-shape keyword (not a real book/periodical title)
    and must be rejected before an archive.org search."""
    return any(kw in title.lower() for kw in _ARCHIVE_TITLE_REJECT_KW)


def _append_archive_candidate(doc: dict, out: "list[Tuple[str, str]]") -> None:
    """Append one archive.org result as a (url, title) pair, normalizing a list-valued title.
    Skips docs with no identifier. Mutates ``out`` in place."""
    identifier = doc.get("identifier")
    if not identifier or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", str(identifier)):
        return  # not an archive.org identifier: never built into a URL
    cand_title = doc.get("title") or ""
    if isinstance(cand_title, list):  # archive.org may return a list
        cand_title = cand_title[0] if cand_title else ""
    out.append((f"https://archive.org/details/{identifier}", str(cand_title)))


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
    if _archive_title_rejected(title):
        return []
    query = _build_archive_query(title, author, periodical)
    # asked once per window, empty answers included (owner 2026-11-20)
    return _as_pairs(
        cached_answer(
            "archive_org_candidates",
            f"{query}|rows={rows}",
            lambda: _archive_candidates(query, rows),
        )
    )


def _as_pairs(answer: Any) -> "list[Tuple[str, str]]":
    """A stored candidate list back as (url, title) pairs (JSON keeps them as lists)."""
    try:
        return [(str(u), str(t)[:500]) for u, t in (answer or [])]
    except (TypeError, ValueError):
        return (
            []
        )  # not a candidate list (a stale or foreign entry): treated as no answer


def _archive_candidates(query: str, rows: int) -> "list[Tuple[str, str]]":
    """One archive.org advancedsearch call (an outage is reported, and gives [])."""
    out: "list[Tuple[str, str]]" = []
    try:
        url = (
            f"https://archive.org/advancedsearch.php?q={quote(query)}"
            f"&fl[]=identifier&fl[]=title&output=json&rows={rows}"
        )
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        response = recorded_fetch(
            "archive-solr",
            url,
            kind=TEXTUAL,
            method="GET",
            timeout=30.0,
            allow_redirects=True,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )
        if _answered("archive_org", "candidates", response):
            docs = response.json().get("response", {}).get("docs", [])
            for doc in docs:
                _append_archive_candidate(doc, out)
    except Exception as e:
        _unreachable("archive_org", "candidates", e)
    return out


def _append_gutenberg_candidate(book: dict, out: "list[Tuple[str, str]]") -> None:
    """Append one Gutendex result as a (url, title) pair: prefer the canonical ebook page, else
    fall back to any text/html format URL (then any format). Skips entries with no URL. Mutates
    ``out`` in place."""
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
    if not title or len(title) < 4:
        return []
    search_term = title if not author else f"{title} {author}"
    # asked once per window, empty answers included (owner 2026-11-20)
    return _as_pairs(
        cached_answer(
            "gutendex_candidates",
            search_term,
            lambda: _gutendex_candidates(search_term),
        )
    )


def _gutendex_candidates(search_term: str) -> "list[Tuple[str, str]]":
    """One Gutendex search (an outage is reported, and gives [])."""
    out: "list[Tuple[str, str]]" = []
    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        response = recorded_fetch(
            "gutendex",
            "https://gutendex.com/books",
            kind=TEXTUAL,
            method="GET",
            params={"search": search_term},
            timeout=30.0,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )
        if _answered("gutendex", "candidates", response):
            for book in response.json().get("results", [])[:5]:
                _append_gutenberg_candidate(book, out)
    except Exception as e:
        _unreachable("gutendex", "candidates", e)
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
    """Fetch metadata and PDF link from Archive.org for a given identifier - once per window,
    a missing item (None) included."""
    from src.utils.search_cache import METADATA_TTL_DAYS

    return cached_answer(
        "archive_org_metadata",
        identifier,
        lambda: _fetch_archive_metadata(identifier),
        ttl_days=METADATA_TTL_DAYS,  # a fresh upload may still be gaining its files
    )


def _fetch_archive_metadata(identifier: str) -> Optional[Dict]:
    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "archive-org",
            f"https://archive.org/metadata/{identifier}",
            kind=TEXTUAL,
            method="GET",
            timeout=30.0,
            result_summary_fn=lambda r: f"metadata status={r.status_code}",
        )
        if not _answered("archive_org", "metadata", resp) or resp.status_code != 200:
            return None
        return _archive_metadata(identifier, resp.json())

    except Exception as e:
        _unreachable("archive_org", "metadata", e)
        return None


def _archive_metadata(identifier: str, data: Dict) -> Dict:
    """The fields kept from an archive.org metadata answer (None values dropped)."""
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


_AMAZON_HOSTS = ("amazon.com", "www.amazon.com")
_AMAZON_PRODUCT = re.compile(
    r"^/(?:[^/?#\\]+/)?(?:dp|gp/product|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?#]|$)"
)


def _clean_listing_text(url: Any) -> bool:
    """A plain, short URL string: no backslash / @ / % and no whitespace or control character."""
    if not isinstance(url, str) or len(url) > 300:
        return False
    if any(c in url for c in ("\\", "@", "%")):
        return False
    return not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)


def _amazon_path(url: str) -> Optional[str]:
    """The URL's path if it is https on exactly amazon.com / www.amazon.com with no userinfo or
    port, else None."""
    from urllib.parse import urlparse

    try:
        parts = urlparse(url)
        plain = (
            not parts.username
            and not parts.password
            and parts.port is None
            and parts.netloc.lower() == (parts.hostname or "")
        )
    except ValueError:
        return None
    ok = parts.scheme == "https" and plain and parts.hostname in _AMAZON_HOSTS
    return parts.path if ok else None


def amazon_listing_url(url: str) -> Optional[str]:
    """The canonical listing URL ``https://www.amazon.com/dp/<ASIN>`` for an Amazon product page,
    else None. Strict, because the result is published and used to build an affiliate link: an
    https URL on exactly amazon.com / www.amazon.com, no userinfo, port, backslash, whitespace or
    control character (a browser reads ``\\`` as ``/``, Python's urlparse does not: the two would
    disagree about the host), and a product path with an ASIN. Everything else - the query string
    (a third party's ``tag=``), fragment, search / review / seller pages - is dropped.
    """
    if not _clean_listing_text(url):
        return None
    path = _amazon_path(url)
    m = _AMAZON_PRODUCT.match(path) if path else None
    return f"https://www.amazon.com/dp/{m.group(1)}" if m else None


def _search_text(value: Optional[str], limit: int = 150) -> str:
    """A title / author for a search query: no quotes, parentheses, or operator characters (the
    text is untrusted book text and must not add ``site:`` / ``-`` terms), capped."""
    text = re.sub(r'["()<>\[\]{}\\|]', " ", str(value or ""))
    text = re.sub(r"(?i)\b(?:site|inurl|intitle|filetype|OR|AND)\b:?", " ", text)
    words = [w.lstrip("-+~") for w in text.split()]  # no exclude / require operators
    return " ".join(w for w in words if w)[:limit].strip()


def search_amazon_candidates(
    title: str,
    author: Optional[str] = None,
    openserp_url: str = "http://localhost:7001",
    limit: int = 5,
) -> "list[Tuple[str, str]]":
    """Amazon listings for a book, as ``(url, title)`` pairs for the caller to verify. A bookseller
    listing is a pointer (to buy, and the source of an affiliate link), NOT a copy: there is no
    expectation of a download. Found through OpenSERP (``site:amazon.com``): Amazon's own pages
    are never fetched, so its terms against scraping are not touched. Asked once per window, an
    empty answer included (``cached_answer``); never raises."""
    title, author = _search_text(title), _search_text(author, 80)
    if len(title) < 4:
        return []
    from src.utils.search_cache import scraped_ttl_days

    return _as_pairs(
        cached_answer(
            "amazon_candidates_v2",  # v2: strict canonical listing URLs
            json.dumps([title, author, limit]),
            lambda: _amazon_candidates(title, author, openserp_url, limit),
            ttl_days=scraped_ttl_days(),  # a scraped engine: empty may mean throttled
        )
    )


def _amazon_candidates(
    title: str, author: Optional[str], openserp_url: str, limit: int
) -> "list[Tuple[str, str]]":
    out: "list[Tuple[str, str]]" = []
    try:
        import time

        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch
        from src.utils.search_query_loader import render_search_queries

        queries = render_search_queries(
            "bibliography", "amazon", title=title, author=author or ""
        )
        time.sleep(
            5
        )  # the OpenSERP politeness gap the other searches keep (a real call only)
        response = recorded_fetch(
            "openserp",
            f"{openserp_url}/mega/search",
            kind=TEXTUAL,
            method="GET",
            params={
                "text": queries[0] if queries else f"{title} site:amazon.com",
                "limit": str(limit * 2),
                "mode": "any",
                "engines": "google,bing,duckduckgo",
            },
            timeout=30.0,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )
        if _answered("openserp", "amazon_search", response):
            for r in response.json().get("results", []):
                listing = amazon_listing_url(str(r.get("url", "")))
                if listing and listing not in {u for u, _ in out}:
                    out.append((listing, str(r.get("title", ""))[:500]))
                if len(out) >= limit:
                    break
    except Exception as e:
        _unreachable("openserp", "amazon_search", e)
    return out


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

        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        response = recorded_fetch(
            "openserp",
            f"{openserp_url}/search",
            kind=TEXTUAL,
            method="POST",
            json={"query": query, "engines": ["google", "bing"]},
            timeout=30.0,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )

        if _answered("openserp", "book_search", response):
            results = response.json().get("results", [])
            if results:
                url = results[0].get("url")
                logger.info("Found via OpenSERP: %s", url)
                return url

    except Exception as e:
        _unreachable("openserp", "book_search", e)

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

    except Exception as e:  # noqa: BLE001 - one failed lookup must not stop resolution
        # Visible, not debug: a failing Grok call here silently turns into "no copy found".
        logger.warning(
            "OBSERV bib.llm_search_failed err=%s status=warn - this citation was not searched "
            "via Grok; check Grok errors in this task's log (it is retried next Phase 3 run)",
            type(e).__name__,
        )

    return None
