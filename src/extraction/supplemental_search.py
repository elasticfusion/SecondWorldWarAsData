"""Search for supplemental material URLs using multiple sources."""

import logging
from typing import Optional, Tuple
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
        query = f"{title} {author or ''} site:gutenberg.org".strip()

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
        # Build query
        query_parts = []
        if title:
            query_parts.append(f'title:"{title}"')
        if author:
            query_parts.append(f'creator:"{author}"')
        if periodical:
            query_parts.append(f'"{periodical}"')

        query = " AND ".join(query_parts) if query_parts else title

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
        prompt = f"""Find the URL for this publication:
Title: {title}
Author: {author or "Unknown"}

Return ONLY the URL, or "NOT_FOUND" if you don't know a reliable URL.
Do not make up URLs. Only return URLs you are confident about."""

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
