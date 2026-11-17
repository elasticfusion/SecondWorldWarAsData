"""Shared Grokipedia helpers — used by every entity that enriches from Grokipedia (people,
places, source_section, equipment, people_groups).

- resolve_grokipedia_url(name): turn a name into a Grokipedia /page/ URL (cached, incl.
  negative). Slugs are read whole and encoded as one path segment (grokipedia_page_url);
  returns None otherwise (null-over-fake).
- fetch_grokipedia_article_text(url): the ARTICLE TEXT. Pages are served server-side, so a
  plain GET carries it; only a shell page falls back to fetch_grokipedia_rendered (headless
  Chromium via Playwright, degrading gracefully to None if the browser is absent).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

import requests

from src.utils.http_text import response_text  # Grokipedia serves undeclared UTF-8

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA}


def grokipedia_page_url(slug: str) -> Optional[str]:
    """The ``/page/`` URL for a scraped slug, or None. Real slugs carry dots, commas and
    parentheses ("James_M._Gavin"), so instead of an allow-list the slug is percent-encoded as
    ONE path segment: '/', '?', '#', '%' and quotes cannot change the path or add a query.
    Empty, "."/".." and over-long values are rejected."""
    from urllib.parse import quote

    if (
        not slug
        or slug in (".", "..")
        or len(slug) > 300
        or any(ord(c) < 32 for c in slug)
    ):
        return None
    return f"https://grokipedia.com/page/{quote(slug, safe='_-.,()')}"


def resolve_grokipedia_url(name: str, timeout: int = 20) -> Optional[str]:
    """Resolve a name to a Grokipedia /page/ URL (clean-slug only). Cached (incl. negative).
    Returns None on a miss — null-over-fake."""
    return resolve_grokipedia_url_status(name, timeout)[0]


def resolve_grokipedia_url_status(
    name: str, timeout: int = 20
) -> Tuple[Optional[str], bool]:
    """(url, transient): like resolve_grokipedia_url, but tells a transient failure (not
    cached; retry later) apart from a genuine miss (negative-cached)."""
    if not name or len(name) < 3:
        return None, False
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("grokipedia_url", name)
    if cached == "NOT_FOUND":
        return None, False
    if cached:
        return cached, False

    url = _scrape_grokipedia_url(name, timeout)
    from src.utils.search_cache import TRANSIENT

    if url is TRANSIENT:
        return (
            None,
            True,
        )  # transient failure: don't negative-cache, don't return the sentinel
    cache_result("grokipedia_url", name, url if url else None)
    return url, False


def _scrape_grokipedia_url(name: str, timeout: int):
    """Return a clean /page/ URL, None on a genuine no-match (200 with no match), or
    search_cache.TRANSIENT on a transient failure (non-200 / network error) so the caller does NOT
    negative-cache a one-off outage for 7 days (which would suppress this name corpus-wide).
    """
    from src.utils.search_cache import TRANSIENT

    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "grokipedia",
            "https://grokipedia.com/search",
            kind=TEXTUAL,
            method="GET",
            params={"q": name},
            headers=_HEADERS,
            timeout=timeout,
            allow_redirects=True,
            result_summary_fn=lambda r: f"status={r.status_code} name={name[:40]!r}",
        )
        if resp.status_code != 200:
            logger.info(
                "OBSERV grokipedia.transient status=%d name=%r",
                resp.status_code,
                name[:60],
            )
            return TRANSIENT
        body = response_text(resp)
        if "/page/" not in body:
            return None  # genuine no-match -> OK to negative-cache
        return first_page_url(body)
    except (requests.RequestException, ValueError) as e:
        logger.info(
            "OBSERV grokipedia.transient err=%s name=%r", str(e)[:80], name[:60]
        )
        return TRANSIENT


def first_page_url(search_html: str) -> Optional[str]:
    """The first real result's /page/ URL in a Grokipedia SEARCH page, or None. ``data-slug``
    carries the raw slug (HTML-escaped: "A_&amp;_B"); an href is percent-encoded and must be read
    WHOLE (the old [A-Za-z0-9_-]+ cut "/page/James_M._Gavin" to the wrong page "James_M"). The
    page's JS template fragments (' + escHtml(s.slug) + ') are skipped."""
    import html
    from urllib.parse import unquote

    candidates = [
        html.unescape(s) for s in re.findall(r'data-slug="([^"]+)"', search_html)
    ]
    candidates += [unquote(h) for h in re.findall(r'href="/page/([^"]+)"', search_html)]
    for slug in candidates:
        url = grokipedia_page_url(slug)
        # the page's JS template fragment (' + escHtml(s.slug) + ') has spaces and '+'; real
        # slugs never do (apostrophes are real: "O'Daniel")
        if url and not any(c in slug for c in '" +'):
            return url
    return None


# --- Headless-rendered article text --------------------------------------------------------

_MAX_RENDERED_CHARS = 8000  # bound the captured article text
# Chrome/nav chrome that leaks into inner_text("body") on Grokipedia; trimmed from the lead.
_CHROME_PREFIXES = ("⌘K", "Suggest edit", "Sign in", "Contents")


def _grokipedia_dedup_skip(url: str) -> bool:
    """Return True if this page was fetched within the 90-day window (skip the render)."""
    # Cross-entity / cross-phase dedup: if this exact Grokipedia page was already
    # fetched within the 90-day window (e.g. a different, unmerged person record for the
    # same name already rendered it), skip the expensive Playwright render.
    try:
        from src.utils import url_registry

        if not url_registry.should_fetch_content(url):
            logger.debug(
                "Grokipedia page already searched (<90d) — skip render: %s", url
            )
            return True
    except Exception:  # noqa: BLE001 - never block enrichment
        pass
    return False


def _render_grokipedia_text(url: str, timeout_ms: int) -> Optional[str]:
    """Render the page with headless Chromium and return raw article text (or None)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # Only reached for a SHELL page (articles are served server-side), so a missing browser
        # loses real text: say so once per process, with the action.
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "grokipedia",
            "playwright_missing",
            key=(),
            every=10**9,
            note=(
                "a Grokipedia page needs the headless render but Playwright/Chromium is not "
                "installed in this image; install it (playwright install chromium) or the page's "
                "text stays unavailable (retried each run)"
            ),
            url=url,
        )
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_UA)
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_timeout(1500)  # let late content settle
                # Prefer the article/main body; fall back to the whole page body.
                text = None
                for sel in ("article", "main", "body"):
                    try:
                        if page.query_selector(sel):
                            text = page.inner_text(sel)
                            break
                    except Exception:  # noqa: BLE001
                        continue
            finally:
                browser.close()
    except (
        Exception
    ) as e:  # noqa: BLE001 - render is best-effort; never block enrichment
        logger.warning("Grokipedia headless render failed for %s: %s", url, e)
        return None
    return text


def _finalize_grokipedia(url: str, result: Optional[str]) -> None:
    """Record the fetch so a duplicate (another entity / phase) is skipped for 90 days."""
    try:
        from src.utils import url_registry

        url_registry.finalize_searched(
            url, relevant=bool(result), context="grokipedia", source="grokipedia"
        )
    except Exception:  # noqa: BLE001
        pass


def fetch_grokipedia_rendered(url: str, timeout_ms: int = 30000) -> Optional[str]:
    """Render a Grokipedia page with headless Chromium (Playwright) and return the article
    text. Grokipedia is a client-side SPA, so a plain HTTP GET returns an empty shell — the
    article text only exists after JavaScript executes, which is what this does.

    GRACEFUL FALLBACK: returns None (never raises) if Playwright or the Chromium browser is
    not installed, or if the render fails/ times out — callers then keep their URL-only
    behavior. Bounded to _MAX_RENDERED_CHARS."""
    if not url:
        return None
    if _grokipedia_dedup_skip(url):
        return None

    text = _render_grokipedia_text(url, timeout_ms)

    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    # Drop a leading run of UI chrome tokens if present.
    for prefix in _CHROME_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    result = cleaned[:_MAX_RENDERED_CHARS] or None
    _finalize_grokipedia(url, result)
    return result


# --- Article text (plain GET first) --------------------------------------------------------

_MAX_PAGE_BYTES = 5_000_000  # a Grokipedia article page is ~0.5 MB; bound the parse


def _article_text_from_html(html: str) -> Optional[str]:
    """The ``<article>`` (else ``<main>``) text of a Grokipedia page, whitespace-collapsed;
    None when the page has NO article region (an unrendered shell)."""
    from bs4 import BeautifulSoup  # pinned dependency (beautifulsoup4)

    soup = BeautifulSoup(html, "html.parser")
    region = soup.find("article") or soup.find("main")
    if region is None:
        return None
    # strip non-text INSIDE the region only (its own <header> carries the title/lead): scripts,
    # and interactive chrome - the text-to-speech voice picker ("Ara Eve Leo Sal") and speed
    # pill ("1x") are <button> menus inside the article header
    for tag in region(
        ["script", "style", "noscript", "svg", "button", "select", "form", "nav"]
    ):
        tag.decompose()
    for menu in region.select("[class~=menu]"):
        menu.decompose()
    return re.sub(r"\s+", " ", region.get_text(" ", strip=True)).strip()


def fetch_grokipedia_article_text(url: str, timeout: int = 30, session: Any = None):
    """The ARTICLE TEXT of a Grokipedia ``/page/`` URL, bounded to _MAX_RENDERED_CHARS.

    Grokipedia serves the article server-side, so a plain GET carries it (XChannel-8 E2E: the
    person path sent the SEARCH page's first 5000 chars - <head>, theme script, CSS - to Grok,
    so Grokipedia contributed no biography for any person). Only a page with no article region
    at all (an unrendered shell) falls back to the headless render. Returns None when there is
    no article text, or ``search_cache.TRANSIENT`` on a non-200 / network error (callers must
    not negative-cache that)."""
    from src.utils.search_cache import TRANSIENT

    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "grokipedia",
            url,
            kind=TEXTUAL,
            method="GET",
            session=session,
            headers=_HEADERS,
            timeout=timeout,
            allow_redirects=True,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )
    except (requests.RequestException, ValueError) as e:
        logger.info("OBSERV grokipedia.transient err=%s url=%s", str(e)[:80], url)
        return TRANSIENT
    if resp.status_code != 200:
        logger.info(
            "OBSERV grokipedia.transient status=%d url=%s", resp.status_code, url
        )
        return TRANSIENT
    # response_text: Grokipedia's UTF-8 decoded correctly (resp.text mojibakes the en dash of
    # "(March 3, 1895 - July 26, 1993)"); the parse is bounded.
    text = _article_text_from_html(response_text(resp)[:_MAX_PAGE_BYTES])
    if text is None:
        # An unrendered shell. The headless render returns None for "no browser here", "render
        # failed" and "rendered <90d ago" alike - none of which proves there is no article, so
        # it is TRANSIENT (retry next run), never a 7-day negative. Remembered per process so
        # one shell page costs at most one render attempt per run.
        if url in _SHELL_UNRENDERABLE:
            return TRANSIENT
        text = fetch_grokipedia_rendered(url)
        if not text:
            _SHELL_UNRENDERABLE.add(url)
            logger.info(
                "OBSERV grokipedia.render_unavailable url=%s (shell page; retried next run)",
                url,
            )
            return TRANSIENT
    return text[:_MAX_RENDERED_CHARS] or None


_SHELL_UNRENDERABLE: set = (
    set()
)  # shell pages whose render yielded nothing, this process
