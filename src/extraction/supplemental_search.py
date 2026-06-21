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
        # Build query — use parenthesized keywords, not exact quotes
        # (Archive.org metadata titles often differ from citation titles)
        query_parts = []
        if title:
            # Strip punctuation that breaks search, keep meaningful words
            words = " ".join(w for w in title.split() if len(w) > 2)
            query_parts.append(f"title:({words})")
        if author:
            # Use last name for personal names, full string for corporate authors
            if any(kw in author.lower() for kw in ["department", "division", "admiralty", "office", "command", "staff"]):
                query_parts.append(f"creator:({author})")
            else:
                # Handle "LastName, FirstName" and "FirstName LastName" formats
                if "," in author:
                    last_name = author.split(",")[0].strip()
                else:
                    parts = author.split()
                    last_name = parts[-1] if len(parts[-1]) > 2 else parts[0]
                query_parts.append(f"creator:({last_name})")
        if periodical:
            query_parts.append(f'"{periodical}"')

        query = " AND ".join(query_parts) if query_parts else title
        query += " AND mediatype:texts"

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


def fetch_archive_org_metadata(identifier: str) -> Optional[Dict]:
    """Fetch metadata and PDF link from Archive.org for a given identifier."""
    try:
        resp = requests.get(
            f"https://archive.org/metadata/{identifier}", timeout=30.0
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        meta = data.get("metadata", {})
        files = data.get("files", [])

        # Find PDF file
        pdf_files = [f for f in files if f.get("name", "").lower().endswith(".pdf")]
        pdf_url = None
        if pdf_files:
            pdf_url = f"https://archive.org/download/{identifier}/{pdf_files[0]['name']}"

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

        # Remove None values
        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        logger.debug("Archive.org metadata fetch failed for %s: %s", identifier, e)
        return None


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
