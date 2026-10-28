"""Enrich places with hierarchy and historical names via Grok + Wikipedia fallback."""

import json
import logging
from pathlib import Path
from typing import Optional

import requests


def _today():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _should_re_search(data: dict) -> bool:
    """Check if a not_found entity should be re-searched based on age."""
    from src.enrichment.enrichment_gate import should_re_search

    return should_re_search(data)


from src.grok_client import BatchModeCollecting, GrokClient
from src.schemas import inject_metadata
from src.utils.file_lock import write_json_with_lock

logger = logging.getLogger(__name__)


def _add_place_sameas(place: dict, extra: Optional[list] = None) -> None:
    """Tier-1/T2-A (deterministic, no new fetch): fold the already-resolved external URLs
    (wikipedia_url / grokipedia_url) + any ``extra`` urls (e.g. the Wikidata QID url) into
    schema.org ``sameAs[]`` — the identity cross-walk.
    Deduped, order-preserving, idempotent. Fail-safe (mutates ``place`` in place)."""
    try:
        existing = place.get("sameAs")
        same: list = list(existing) if isinstance(existing, list) else []
        candidates = [place.get("wikipedia_url"), place.get("grokipedia_url")]
        candidates += list(extra or [])
        for u in candidates:
            if isinstance(u, str) and u.strip() and u not in same:
                same.append(u.strip())
        if same:
            place["sameAs"] = same
    except (
        Exception
    ) as e:  # noqa: BLE001 - provenance enrichment must never crash the pass
        logger.debug("sameAs derivation skipped: %s", e)


SKIP_FILES = frozenset(
    ["index.json", "duplicate_report.json", ".processed_events.json"]
)

_WIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

_WIKI_API = "https://en.wikipedia.org/w/api.php"

PROMPT = """Look up this WWII-era geographic location: {name}

Return JSON with:
- continent: Which continent (Europe, Asia, Africa, North America, South America, Oceania)
- country: Country name (use WWII-era country if different from modern, e.g. "Germany" not "West Germany")
- region: Administrative region/state/province (e.g. "Normandy", "Bavaria"). null if unknown
- historical_names: Array of {{"name": "...", "language": "English|German|French|Russian|Italian|Japanese|Polish|Other", "date_range": "1939-1945"}} for names used during WWII that differ from the current name. Empty array if name unchanged.

CRITICAL: Only include facts you are confident about. Use null for unknown fields.
Return ONLY valid JSON, no markdown."""

_CONTINENT_KEYWORDS = {
    "Europe": ["Europe", "European"],
    "Asia": ["Asia", "Asian"],
    "Africa": ["Africa", "African"],
    "North America": ["North America", "United States", "Canada", "Mexico"],
    "South America": ["South America"],
    "Oceania": ["Oceania", "Pacific", "Australia"],
}


def _build_wiki_place_result(name: str, page_data: dict) -> dict:
    """Build the Wikipedia place result dict from one page: resolve URL, image + license, and
    the {country, continent, region} hierarchy (parsed from the SAME fetch's categories).
    Records the resolved URL in the url_registry so a duplicate is skipped for 90 days.
    """
    page_title = page_data.get("title", name)
    img_url = page_data.get("original", {}).get("source", "")
    license_info = None
    if img_url:
        filename = img_url.rsplit("/", 1)[-1]
        license_info = _fetch_image_license(filename)
    resolved_url = f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"
    # T2-A: the Wikidata QID comes back in pageprops (piggybacked on this same fetch — no new
    # request). Turn it into a canonical wikidata URL for the sameAs cross-walk. Confident-only.
    qid = (page_data.get("pageprops") or {}).get("wikibase_item")
    wikidata_url = (
        f"https://www.wikidata.org/wiki/{qid}"
        if isinstance(qid, str) and qid.strip()
        else None
    )
    # Record the fetch (resolved URL) so a duplicate is skipped for 90 days.
    try:
        from src.utils import url_registry as _reg

        _reg.finalize_searched(
            resolved_url, relevant=True, context="wikipedia", source="wikipedia"
        )
    except Exception:  # noqa: BLE001
        pass
    return {
        "wikipedia_url": resolved_url,
        "wikidata_url": wikidata_url,
        "image": img_url,
        "license": license_info or "unknown",
        # Structured hierarchy parsed from the SAME fetch (categories) — this is
        # what Phase 3 used to make a second Wikipedia call for.
        "hierarchy": _parse_wiki_page(page_data),
    }


def _fetch_place_wikipedia_full(name: str) -> Optional[dict]:
    """Fetch Wikipedia URL + image + structured hierarchy for a place in ONE request.

    Requests categories alongside the extract/image so the Phase-2 place Wikipedia fetch
    also yields the {country, continent, region} hierarchy — Phase 3 no longer makes a
    second Wikipedia call for that."""
    # URL-keyed dedup (NOT entity-keyed): if the canonical article URL for this name was
    # already fetched within the 90-day window (any entity / either phase), skip.
    _canon = f"https://en.wikipedia.org/wiki/{name.replace(' ', '_')}"
    try:
        from src.utils import url_registry

        if not url_registry.should_fetch_content(_canon):
            return None
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "wikipedia",
            _WIKI_API,
            kind=TEXTUAL,
            method="GET",
            params={  # type: ignore[arg-type]
                "action": "query",
                "format": "json",
                "titles": name,
                "prop": "extracts|pageimages|categories|pageprops",
                "ppprop": "wikibase_item",
                "exintro": "True",
                "explaintext": "True",
                "redirects": "1",
                "piprop": "original",
                "cllimit": 20,
            },
            headers=_WIKI_HEADERS,
            timeout=15,
            result_summary_fn=lambda r: f"status={r.status_code} place={name[:40]!r}",
        )
        if resp.status_code != 200:
            return None
        pages = resp.json().get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if page_id == "-1" or page_data.get("missing") is not None:
                continue
            return _build_wiki_place_result(name, page_data)
    except Exception:
        pass
    return None


def _fetch_image_license(filename: str) -> Optional[str]:
    """Fetch license from Wikimedia Commons."""
    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "wikimedia-commons",
            "https://commons.wikimedia.org/w/api.php",
            kind=TEXTUAL,
            method="GET",
            params={
                "action": "query",
                "format": "json",
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "extmetadata",
            },
            headers=_WIKI_HEADERS,
            timeout=10,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for page_data in pages.values():
                ii = page_data.get("imageinfo", [])
                if ii:
                    meta = ii[0].get("extmetadata", {})
                    return meta.get("LicenseShortName", {}).get("value", "unknown")
    except Exception:
        pass
    return None


def enrich_place_from_grokipedia(place: dict) -> bool:
    """Phase-2 in-place Grokipedia descriptive lookup for a single place record.

    Reuses _search_grokipedia_place (the single source of the Grokipedia HTTP code — we
    do NOT duplicate it) to set grokipedia_url on the place record. A
    grokipedia_checked_at marker is stamped even on a MISS so we never re-fetch, and the
    step is GATED on grokipedia_checked_at OR an existing grokipedia_url (idempotent
    across runs). Performs NO file I/O — the caller (extractor) owns the native save
    path. Hierarchy/names + geocoding remain a Phase-3 concern.

    Returns True if a Grokipedia URL was applied, else False.
    """
    from datetime import datetime, timezone

    # Gate: already have a URL, or already checked (hit or miss) -> never re-fetch.
    if place.get("grokipedia_url") or place.get("grokipedia_checked_at"):
        return False

    place["grokipedia_checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    name = place.get("current_name") or place.get("name", "")
    if not name:
        return False

    url = _search_grokipedia_place(name)
    if not url:
        logger.debug("No Grokipedia page for place %s", name)
        return False

    place["grokipedia_url"] = url
    _add_place_sameas(place)  # Tier-1: fold into sameAs[]
    logger.debug("Grokipedia enriched place %s → %s", name, url)
    return True


def enrich_place_from_wikipedia(place: dict) -> bool:
    """Phase-2 in-place Wikipedia descriptive lookup (text URL + image) for a place.

    Reuses _fetch_place_wikipedia_full (the single source of the place-Wikipedia HTTP
    code — not duplicated) to set wikipedia_url + an image entry under images. A
    wikipedia_checked_at marker is stamped even on a MISS so we never re-fetch, and the
    step is GATED on wikipedia_url OR wikipedia_checked_at (idempotent across runs). This
    mirrors the equipment/groups Phase-2 Wikipedia pattern. Performs NO file I/O — the
    caller (extractor) owns the native save path. Hierarchy/names (Grok + category
    parsing) and geocoding remain a Phase-3 concern.

    Returns True if a Wikipedia URL was applied, else False.
    """
    from datetime import date

    # Gate: already have a URL, or already checked (hit or miss) -> never re-fetch.
    if place.get("wikipedia_url") or place.get("wikipedia_checked_at"):
        return False

    place["wikipedia_checked_at"] = date.today().isoformat()

    name = place.get("current_name") or place.get("name", "")
    if not name:
        return False

    wiki_data = _fetch_place_wikipedia_full(name)
    if not wiki_data or not wiki_data.get("wikipedia_url"):
        logger.debug("No Wikipedia page for place %s", name)
        return False

    place["wikipedia_url"] = wiki_data["wikipedia_url"]
    # T2-A: the Wikidata QID url goes into sameAs[] (NOT a new top-level field — places schema is
    # additionalProperties:false and has no wikidata_url slot). sameAs is exactly the cross-walk home.
    _add_place_sameas(place, extra=[wiki_data.get("wikidata_url")])
    if wiki_data.get("image") and not place.get("images"):
        place.setdefault("images", []).insert(
            0,
            {
                "url": wiki_data["image"],
                "license": wiki_data.get("license", "unknown"),
                "source": "wikipedia",
            },
        )
    # Structured hierarchy came from the SAME fetch (categories) — apply it here so Phase 3
    # no longer needs a second Wikipedia call for it.
    hierarchy = wiki_data.get("hierarchy")
    if hierarchy:
        _merge_hierarchy(place, hierarchy)
    logger.debug("Wikipedia enriched place %s → %s", name, wiki_data["wikipedia_url"])
    return True


def _search_grokipedia_place(name: str) -> Optional[str]:
    """Search Grokipedia for a place. Returns page URL or None."""
    import re

    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("grokipedia_place", name)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    try:
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "grokipedia",
            f"https://grokipedia.com/search?q={name} World War",
            kind=TEXTUAL,
            method="GET",
            headers=_WIKI_HEADERS,
            timeout=15,
            result_summary_fn=lambda r: f"status={r.status_code} place={name[:40]!r}",
        )
        if resp.status_code == 200 and "/page/" in resp.text:
            # Find page links matching the place name
            page_links = re.findall(r'href="/page/([^"]+)"', resp.text)
            name_lower = name.lower().replace(" ", "")
            for link in page_links:
                link_lower = link.lower().replace("_", "")
                if name_lower in link_lower or link_lower in name_lower:
                    url = f"https://grokipedia.com/page/{link}"
                    cache_result("grokipedia_place", name, url)
                    return url
    except Exception:
        pass
    cache_result("grokipedia_place", name, None)
    return None


def _parse_wiki_page(page: dict) -> dict:
    """Extract hierarchy from Wikipedia page extract and categories."""
    extract = page.get("extract", "")
    cats = [c.get("title", "") for c in page.get("categories", [])]
    cat_text = " ".join(cats)
    combined = f"{extract} {cat_text}"

    result: dict = {}

    # Detect continent
    for continent, keywords in _CONTINENT_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            result["continent"] = continent
            break

    # Extract country from categories like "Category:Cities in France"
    result["country"] = _extract_country_from_cats(cats)

    return result if result.get("continent") or result.get("country") else {}


def _extract_country_from_cats(cats: list) -> Optional[str]:
    """Extract country name from Wikipedia categories."""
    for cat in cats:
        title = cat.replace("Category:", "")
        for prefix in (
            "Cities in ",
            "Towns in ",
            "Villages in ",
            "Populated places in ",
            "Geography of ",
        ):
            if prefix in title:
                return title.split(prefix, 1)[1].split(",")[0].strip()
    return None


def _merge_hierarchy(data, enrichment):
    """Merge hierarchy fields from enrichment into data. Returns True if changed."""
    hierarchy = data.get("hierarchy") or {}
    changed = False
    for field in ("continent", "country", "region"):
        val = enrichment.get(field)
        if val and not hierarchy.get(field):
            hierarchy[field] = val
            changed = True
    if changed:
        data["hierarchy"] = hierarchy
    return changed


def _merge_historical_names(data, enrichment):
    """Merge historical names from enrichment. Returns True if changed."""
    hist = enrichment.get("historical_names")
    if not isinstance(hist, list) or not hist:
        return False
    existing = {
        h["name"]
        for h in data.get("historical_names", [])
        if isinstance(h, dict) and "name" in h
    }
    changed = False
    for entry in hist:
        if isinstance(entry, dict) and entry.get("name") not in existing:
            data.setdefault("historical_names", []).append(entry)
            changed = True
    return changed


def _apply_enrichment(data, enrichment):
    """Apply enrichment data to place record."""
    if not isinstance(enrichment, dict):
        return False
    h = _merge_hierarchy(data, enrichment)
    n = _merge_historical_names(data, enrichment)
    return h or n


def _needs_enrichment(data):
    """Check if place still needs hierarchy enrichment."""
    hierarchy = data.get("hierarchy") or {}
    return not (hierarchy.get("continent") and hierarchy.get("country"))


def _try_grok(name, grok_client):
    """Try Grok enrichment. Returns a dict, or None for a clean no-result.

    Raises on a TRANSIENT error (so the caller does not cache it as a durable
    'not_found'); BatchModeCollecting propagates separately as usual."""
    try:
        return grok_client.extract_json(
            prompt=PROMPT.format(name=name),
            use_cache=True,
            cache_type="place_enrichment",
        )
    except BatchModeCollecting:
        raise
    except Exception as exc:
        logger.warning("Grok place-enrichment errored for %s: %s", name, exc)
        raise  # M3: transient error must NOT be recorded as a clean not_found


def _stamp_place_status(
    data: dict, name: str, changed: bool, errored: bool
) -> Optional[bool]:
    """Apply the enrichment outcome to ``data`` (in place) and decide the return value.

    - changed  -> stamp enriched + search date, return True.
    - errored  -> TRANSIENT: do NOT stamp (so it retries), return False (caller returns early).
    - neither  -> clean negative: stamp not_found + search date, return None (continue to write).
    """
    if changed:
        data["enrichment_status"] = "enriched"
        data["last_enrichment_search"] = _today()
        return True
    if errored:
        # M3: a transient source error is NOT a clean negative — do NOT stamp
        # not_found/last_enrichment_search (that would suppress retries for the
        # whole re-search window). Leave the entity unstamped so it retries.
        logger.warning("Place '%s' enrichment errored — leaving for retry", name)
        return False
    data["enrichment_status"] = "not_found"
    data["last_enrichment_search"] = _today()
    return None


def _place_needs_work(place_file: Path) -> bool:
    """Cheap pre-scan mirroring enrich_place's gates: True if this place would actually attempt
    enrichment (readable + named + needs hierarchy + not a fresh not_found). Used to gate the
    coverage anomaly so a benign re-run of an already-enriched corpus doesn't false-fire.
    """
    try:
        data = json.loads(place_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    name = data.get("current_name") or data.get("name", "")
    if not name or not _needs_enrichment(data):
        return False
    if data.get("enrichment_status") == "not_found" and not _should_re_search(data):
        return False
    return True


def enrich_place(place_file: Path, grok_client: GrokClient) -> bool:
    """Enrich a single place file. Returns True if enriched."""
    try:
        data = json.loads(place_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to load %s: %s", place_file.name, exc)
        return False

    name = data.get("current_name") or data.get("name", "")
    if not name or not _needs_enrichment(data):
        return False

    if data.get("enrichment_status") == "not_found":
        if not _should_re_search(data):
            logger.debug("Previously searched, not found: %s", name)
            return False
        logger.info("Re-searching (stale not_found): %s", name)

    logger.info("Enriching place: %s", name)
    try:
        changed, errored = _enrich_place_data(data, name, grok_client)
    except BatchModeCollecting:
        return False
    status = _stamp_place_status(data, name, changed, errored)
    if status is False:
        return False
    inject_metadata(data, entity="places")
    write_json_with_lock(place_file, data, entity="places")
    if changed:
        logger.info("  ✓ Enriched %s", name)
    return changed


def _enrich_place_data(data, name, grok_client):
    """Try Grok then Wikipedia then Grokipedia to enrich place.

    Returns ``(changed, errored)``: ``errored`` is True if a source raised a
    transient error (so the caller must NOT stamp a durable ``not_found`` — the
    entity should be retried next run)."""
    errored = False
    try:
        enrichment = _try_grok(name, grok_client)
    except BatchModeCollecting:
        raise
    except Exception:  # noqa: BLE001 - transient; already logged in _try_grok
        enrichment = None
        errored = True
    changed = _apply_enrichment(data, enrichment) if enrichment else False
    # Wikipedia hierarchy parsing MOVED TO PHASE 2 (enrich_place_from_wikipedia parses the
    # {country, continent, region} hierarchy from the SAME Wikipedia fetch that gets the
    # descriptive URL/image). Phase 3 no longer makes a second Wikipedia call — it keeps
    # ONLY the Grok structured-facts enrichment above + geocoding (a separate step). Phase 3
    # is now Grokipedia/Wikipedia-free.
    return changed, errored


def _collect_place_enrichments(pool, futures, total: int) -> int:
    """Drain the place-enrichment futures, counting successes + logging progress. Returns the
    enriched count."""
    from concurrent.futures import as_completed

    enriched = 0
    processed = 0
    for future in as_completed(futures):
        processed += 1
        try:
            if future.result():
                enriched += 1
        except Exception as e:
            logger.warning("Failed to enrich place %s: %s", futures[future].stem, e)
        if processed % 10 == 0 or processed == total:
            logger.info(
                "  Places progress: %d/%d done (%d enriched)",
                processed,
                total,
                enriched,
            )
    return enriched


def enrich_all_places(
    places_dir: Path,
    grok_client: GrokClient,
    max_places: Optional[int] = None,
    max_workers: int = 6,
) -> int:
    """Enrich all places. Returns count enriched."""
    if not places_dir.exists():
        logger.error("Directory not found: %s", places_dir)
        return 0

    files = [f for f in places_dir.glob("*.json") if f.name not in SKIP_FILES]
    if max_places:
        files = files[:max_places]

    logger.info("Enriching %d places...", len(files))

    from concurrent.futures import ThreadPoolExecutor

    total = len(files)
    # Snapshot how many places NEED work BEFORE the enrichment loop — _place_needs_work returns
    # False for a just-enriched record, so counting it AFTER the loop always yields ~0 (a misleading
    # summary). Measuring up-front gives a truthful "needed" and a correct anomaly gate.
    needed = sum(1 for f in files if _place_needs_work(f))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich_place, f, grok_client): f for f in files}
        enriched = _collect_place_enrichments(pool, futures, total)

    logger.info("Place enrichment complete: %d/%d enriched", enriched, len(files))
    # Canonical coverage rollup + anomaly, gated on how many places actually NEEDED work (so a
    # benign re-run of an already-enriched corpus does not false-fire).
    logger.info(
        "OBSERV places.summary total=%d needed=%d enriched=%d skipped=%d",
        total,
        needed,
        enriched,
        total - enriched,
    )
    if needed > 0 and enriched == 0:
        logger.warning(
            "OBSERV places.anomaly 0-of-%d enrichable places enriched "
            "(hierarchy/historical-name enrichment failed/unreachable?)",
            needed,
        )
    return enriched
