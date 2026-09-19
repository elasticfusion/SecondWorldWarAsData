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
    "You are a historical military geographer for the WWII European Theater "
    "(Western Front, 1944-1945). Some place names are situational tactical "
    "features (a gap, corridor, ridge, bridgehead, sector) named relative to a "
    "known place rather than a mapped settlement. Using the encyclopedic context "
    "and the operation's nearby places, give an APPROXIMATE center coordinate "
    "for the feature. If you cannot place it, set found=false rather than guess."
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
    cache_result("grokipedia_place", term, extract or None)
    return extract


def _grokipedia_fetch(term: str, session: Any) -> Optional[str]:
    """Search Grokipedia for ``term`` and return prose from the top page."""
    try:
        resp = session.get(
            _GROKIPEDIA_SEARCH,
            params={"q": f"{term} World War II"},
            headers=_BROWSER_UA,
            timeout=20,
            allow_redirects=True,
        )
        if resp.status_code != 200 or "/page/" not in resp.text:
            return None
        slugs = _SLUG_RE.findall(resp.text)
        if not slugs:
            return None
        page = session.get(_GROKIPEDIA_PAGE + slugs[0], headers=_BROWSER_UA, timeout=20)
        if page.status_code != 200:
            return None
    except Exception as exc:  # noqa: BLE001 - best-effort lookup
        logger.warning("Grokipedia lookup failed for %s: %s", term, exc)
        return None
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

    extract = _wiki_extract(term, session) or _wiki_extract(
        f"{term} (World War II)", session
    )
    cache_result("wikipedia_situational", term, extract or None)
    return extract


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


def _wiki_extract(title: str, session: Any) -> Optional[str]:
    """Fetch the intro extract of a Wikipedia page, or None."""
    try:
        resp = session.get(
            _WIKI_ENDPOINT,
            params={
                "action": "query",
                "format": "json",
                "titles": title,
                "prop": "extracts",
                "exintro": "True",
                "explaintext": "True",
                "redirects": "1",
            },
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        pages = resp.json().get("query", {}).get("pages", {})
    except Exception as exc:  # noqa: BLE001 - lookup is best-effort
        logger.warning("Wikipedia lookup failed for %s: %s", title, exc)
        return None
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
