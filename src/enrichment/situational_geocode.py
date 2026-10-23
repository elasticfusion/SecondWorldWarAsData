"""Geocode *situational* place names — tactical geography relative to a known place.

A third class of place appears in operational histories that is neither a modern
settlement (OSM handles those) nor a numbered height (the hill geocoder handles
those): a *situational* feature named for its role in the fighting — "Aachen
Gap", "the Roer bridgehead", "Martinville ridge", "the Stolberg corridor". These
have no gazetteer entry, but they are real, documented terrain relative to an
anchor place, and an encyclopedic lookup plus the operation's context is usually
enough to place them approximately.

Pipeline per situational place:

1. Look the term up on Wikipedia (cached) for an encyclopedic extract — e.g.
   "Aachen Gap" -> the Stolberg Corridor between the Hürtgen and the Dutch
   border. Also try the term with a "(World War II)" disambiguation suffix.
2. Combine that extract with the record's own event context (nearby named
   places) and ask Grok for an approximate coordinate and the reasoning.
3. Mark the result approximate and flagged for review — these are areas, not
   points, so a single coordinate is only a centroid.

Best-effort by nature (it will not resolve everything), but it recovers places
the other two geocoders cannot. Returns the shared :class:`GeocodeResult` so it
composes with the cascade; intended as a fallback after OSM and the hill
geocoder, before giving up.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from src.enrichment.places_grok_geocode import GeocodeResult, _parse_geocode_reply

logger = logging.getLogger(__name__)

_WIKI_ENDPOINT = "https://en.wikipedia.org/w/api.php"
_HEADERS = {"User-Agent": "SecondWorldWarAsData/1.0 (historical place geocoding)"}
# Grokipedia is a web app (no public JSON API); we search then fetch a page,
# mirroring the project's existing Grokipedia usage in enrich_biographies.
_GROKIPEDIA_SEARCH = "https://grokipedia.com/search"
_GROKIPEDIA_PAGE = "https://grokipedia.com/page/"
_BROWSER_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}
_SLUG_RE = re.compile(r'data-slug="([^"]+)"')
# Strip HTML tags to recover readable prose from a fetched page.
_TAG_RE = re.compile(r"<[^>]+>")

_SYSTEM_PROMPT = (
    "You are a historical military geographer for World War II (any theater — "
    "European, Mediterranean, North African, Pacific, CBI). Some place names are "
    "situational tactical features (a gap, corridor, ridge, beachhead, bridgehead, "
    "sector) named relative to a known place rather than a mapped settlement. Using "
    "the encyclopedic context and the operation's nearby places (which fix the "
    "region/theater), give an APPROXIMATE center coordinate for the feature. If you "
    "cannot place it, set found=false rather than guess."
)


def grokipedia_context(term: str, session: Any) -> Optional[str]:
    """Return a Grokipedia page extract for ``term``, or None.

    Searches Grokipedia, follows the best matching ``/page/<slug>`` result, and
    returns readable text from that page. Cached via the project's search cache.
    Grokipedia has no public API, so this scrapes the web app the same way the
    existing biography enrichment does.
    """
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("grokipedia_place", term)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    extract = _grokipedia_fetch(term, session)
    from src.utils.search_cache import TRANSIENT

    if extract is TRANSIENT:
        return None  # transient: don't negative-cache
    cache_result("grokipedia_place", term, extract or None)
    return extract


def _grokipedia_fetch(term: str, session: Any):
    """Search Grokipedia for ``term`` and return prose from the top page, None on a genuine no-match,
    or search_cache.TRANSIENT on a non-200/exception (so the caller does NOT negative-cache a one-off
    outage for 7d)."""
    from src.utils.search_cache import TRANSIENT

    try:
        from src.utils.fetch_provenance import STRUCTURED, recorded_fetch

        resp = recorded_fetch(
            "grokipedia",
            _GROKIPEDIA_SEARCH,
            kind=STRUCTURED,
            method="GET",
            params={"q": f"{term} World War II"},
            session=session,
            headers=_BROWSER_UA,
            timeout=20,
            allow_redirects=True,
            result_summary_fn=lambda r: f"status={r.status_code} term={term[:40]!r}",
        )
        if resp.status_code != 200:
            logger.info(
                "OBSERV situational.grokipedia_transient status=%d term=%r",
                resp.status_code,
                term[:60],
            )
            return TRANSIENT
        if "/page/" not in resp.text:
            return None  # genuine no-match
        slugs = _SLUG_RE.findall(resp.text)
        if not slugs:
            return None
        page = recorded_fetch(
            "grokipedia",
            _GROKIPEDIA_PAGE + slugs[0],
            kind=STRUCTURED,
            method="GET",
            session=session,
            headers=_BROWSER_UA,
            timeout=20,
            result_summary_fn=lambda r: f"page status={r.status_code}",
        )
        if page.status_code != 200:
            logger.info(
                "OBSERV situational.grokipedia_transient page_status=%d term=%r",
                page.status_code,
                term[:60],
            )
            return TRANSIENT
    except Exception as exc:  # noqa: BLE001 - best-effort lookup
        logger.info(
            "OBSERV situational.grokipedia_transient err=%s term=%r",
            str(exc)[:80],
            term[:60],
        )
        return TRANSIENT
    text = _TAG_RE.sub(" ", page.text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:800] if text else None


def wikipedia_context(term: str, session: Any) -> Optional[str]:
    """Return a short Wikipedia extract for ``term``, or None.

    Cached via the project's search cache. Tries the bare term, then a
    ``(World War II)`` disambiguation, matching the existing wiki-lookup modules.
    """
    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("wikipedia_situational", term)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    from src.utils.search_cache import TRANSIENT

    extract = None
    transient = False
    for title in (term, f"{term} (World War II)"):
        r = _wiki_extract(title, session)
        if r is TRANSIENT:
            transient = True
            continue
        if r:
            extract = r
            break
    if extract:
        cache_result("wikipedia_situational", term, extract)
        return extract
    if not transient:
        cache_result("wikipedia_situational", term, None)  # genuine miss
    return None


def encyclopedia_context(term: str, session: Any) -> tuple[Optional[str], str]:
    """Return (extract, source) with Grokipedia strictly preferred over Wikipedia.

    Grokipedia is the primary source and Wikipedia is the fallback: Grokipedia is
    tried for *every* lookup candidate (the full term, then its anchor place)
    before Wikipedia is consulted at all. Situational names like "Aachen Gap"
    often have no encyclopedia page while their anchor ("Aachen") does, so both
    forms are tried within each source. Returns the source tag ("grokipedia",
    "wikipedia", or "none") for provenance.
    """
    candidates = _lookup_terms(term)
    for query in candidates:
        grok = grokipedia_context(query, session)
        if grok:
            return grok, "grokipedia"
    for query in candidates:
        wiki = wikipedia_context(query, session)
        if wiki:
            return wiki, "wikipedia"
    return None, "none"


# Trailing tactical-feature words dropped to recover the anchor place from a
# situational name (e.g. "Aachen Gap" -> "Aachen").
_FEATURE_WORDS = frozenset(
    {
        "gap",
        "corridor",
        "ridge",
        "bridgehead",
        "sector",
        "area",
        "salient",
        "pocket",
        "line",
        "front",
        "highway",
        "railroad",
        "autobahn",
        "forest",
        "valley",
        "heights",
        "plain",
    }
)


def _lookup_terms(term: str) -> List[str]:
    """Yield lookup candidates: the full term, then its anchor place if distinct.

    The anchor is the term with a trailing feature word removed ("Aachen Gap"
    -> "Aachen"); anchors reliably have encyclopedia pages when the situational
    name does not.
    """
    terms = [term]
    words = term.split()
    if len(words) >= 2 and words[-1].lower() in _FEATURE_WORDS:
        anchor = " ".join(words[:-1])
        if anchor and anchor != term:
            terms.append(anchor)
    return terms


def _wiki_extract(title: str, session: Any):
    """Fetch the intro extract of a Wikipedia page, None on a genuine miss, or search_cache.TRANSIENT
    on a non-200/exception (so the caller does NOT negative-cache a one-off outage)."""
    from src.utils.search_cache import TRANSIENT

    try:
        from src.utils.fetch_provenance import STRUCTURED, recorded_fetch

        resp = recorded_fetch(
            "wikipedia",
            _WIKI_ENDPOINT,
            kind=STRUCTURED,
            method="GET",
            params={
                "action": "query",
                "format": "json",
                "titles": title,
                "prop": "extracts",
                "exintro": "True",
                "explaintext": "True",
                "redirects": "1",
            },
            session=session,
            headers=_HEADERS,
            timeout=15,
            result_summary_fn=lambda r: f"status={r.status_code} title={title[:40]!r}",
        )
        if resp.status_code != 200:
            logger.info(
                "OBSERV situational.wiki_transient status=%d title=%r",
                resp.status_code,
                title[:60],
            )
            return TRANSIENT
        pages = resp.json().get("query", {}).get("pages", {})
    except Exception as exc:  # noqa: BLE001 - lookup is best-effort
        logger.info(
            "OBSERV situational.wiki_transient err=%s title=%r",
            str(exc)[:80],
            title[:60],
        )
        return TRANSIENT
    for page_id, page_data in pages.items():
        if page_id == "-1":
            continue
        extract = page_data.get("extract", "")
        if extract:
            return extract[:800]
    return None


def _anchors(place: dict) -> List[str]:
    """Collect nearby-place context strings from a record's event mentions."""
    anchors: List[str] = []
    for mention in place.get("event_mentions", []) or []:
        for key in ("Sub_event_Name", "original_text", "Event_Name"):
            val = mention.get(key)
            if val and val not in anchors:
                anchors.append(str(val))
    return anchors[:3]


def _prompt_for(name: str, wiki: Optional[str], anchors: List[str]) -> str:
    """Build the situational-geocoding prompt from encyclopedia + event context."""
    wiki_block = f"Encyclopedic context:\n{wiki}\n" if wiki else ""
    ctx = "\n".join(f"- {a}" for a in anchors) if anchors else "- (none)"
    return (
        f"Situational feature: {name!r}.\n"
        f"{wiki_block}"
        f"Operation's nearby places:\n{ctx}\n"
        "Give an APPROXIMATE center coordinate. Output ONLY compact JSON: "
        '{"found": true, "latitude": 50.85, "longitude": 6.2, '
        '"country": "Germany", "confidence": 0.5, '
        '"note": "what the feature is and the anchor used"}'
    )


def make_situational_geocoder(session: Any = None):
    """Return a ``geocode(name, place, grok_client) -> GeocodeResult`` callable.

    Looks up encyclopedic context (Grokipedia first, Wikipedia fallback), then
    asks Grok for an approximate center. Results are always marked approximate
    (confidence capped) and noted with the context source — these are areas, not
    points — so they surface for review rather than being trusted as precise.
    """
    if session is None:
        from src.utils.http_pool import get_session

        session = get_session()

    def geocode(name: str, place: dict, grok_client: Any) -> GeocodeResult:
        context, ctx_source = encyclopedia_context(name, session)
        prompt = _prompt_for(name, context, _anchors(place))
        try:
            raw = grok_client.chat_completion(
                prompt=prompt,
                system_prompt=_SYSTEM_PROMPT,
                temperature=0.1,
                cache_type="places",
            )
        except Exception as exc:  # noqa: BLE001 - surface as miss, keep batch alive
            logger.warning("Situational geocode failed for %s: %s", name, exc)
            return GeocodeResult(
                found=False, note=f"grok error: {exc}", source="situational"
            )
        result = _parse_geocode_reply(raw)
        result.source = "situational"
        if result.found:
            # These are areas, not points — cap confidence and always flag.
            result.confidence = min(result.confidence, 0.5)
            base = result.note or ""
            result.note = (
                f"approximate situational center (context: {ctx_source}); {base}"
            ).strip("; ")
        return result

    return geocode
