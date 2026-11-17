"""Equipment identity: which records are SPECIFIC enough to enrich, and which named vessel a name is.

Owner rules (2026-11-07, XChannel-8 E2E):
- A GENERIC mention ("cannon", "trench", "rifle", "105-mm howitzer") stays in the JSON (record +
  mentions + provenance) but is never searched on Grokipedia/Wikipedia or for images. Only SPECIFIC
  equipment ("M1 Garand", "Sherman tank", "LCVP", "B-24") is enriched. The old gate treated any
  ``technical_identifier`` as specific, and the extractor fills that field with the bare name
  ("trench"), so every generic word was enriched and illustrated (Mariana Trench for "trench").
- A NAMED VESSEL ("Quincy") must be disambiguated to the ship that existed AT THE MENTION'S DATE:
  there were two WWII USS Quincys (CA-39 sunk Aug 1942; CA-71 at Normandy). Hull numbers are not in
  the text, so candidates come from Wikipedia's ship index page and are filtered by their infobox
  service dates; Grok may only CHOOSE among candidates. Unresolved -> needs_review, never searched.
- "World War II" qualifies TEXT (the Wikipedia/Grokipedia JSON), never images (images: vision).
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SPECIFIC = "specific"
GENERIC = "generic"
UNRESOLVED = "unresolved"  # a named vessel whose ship could not be determined

# --- WWII qualification of TEXT -----------------------------------------------------------------

_WWII = re.compile(
    r"\bworld\s+war\s+(?:ii|2|two)\b|\bsecond\s+world\s+war\b|\bww\s?(?:ii|2)\b"
    r"|\b1939\s*[-\u2013\u2014]\s*(?:19)?45\b",
    re.IGNORECASE,
)


def mentions_wwii(text: Optional[str]) -> bool:
    """True when an encyclopedia text places its subject in WWII: it names the war (World War II /
    Second World War / WWII) or the 1939-1945 span. A lone year is not enough (a post-war design's
    article can mention "1944" once)."""
    return bool(text) and _WWII.search(text or "") is not None


# --- specific vs generic (deterministic) --------------------------------------------------------

# Craft / weapon class acronyms that name ONE type on their own. An allow-list: an open
# "any capitals" rule made "AA gun", "AT gun", "HE shell", "US rifle", "SS troops" specific.
_CLASS_ACRONYMS = (
    "LST",
    "LSI",
    "LSM",
    "LSD",
    "LCT",
    "LCVP",
    "LCI",
    "LCM",
    "LCA",
    "LCP",
    "LCS",
    "LCF",
    "LCG",
    "LCR",
    "LCC",
    "LVT",
    "DUKW",
    "PT",
    "MTB",
    "MGB",
    "PIAT",
    "BAR",
    "AVRE",
    "DD",
)

# A designation token: letters+digits (M1, M4A3, B-24, P-47, Bf 109, Ju 87, Mk IV, Type 97,
# Flak 36, Pak 40, Sd.Kfz. 251), a class acronym (LST, LCT, LCVP, LCI(L), DUKW, DD), or an
# X-boat class (E-boat, U-boat). Calibers ("105-mm") and ordinals ("8th") are NOT designations.
_DESIGNATION = re.compile(
    r"\b(?:[A-Z]{1,3}[a-z]?[- ]?\d{1,4}[A-Z\d]{0,4}(?!\s*-?\s*mm\b)(?!th\b|st\b|nd\b|rd\b))"
    r"|\b(?:Mk|Mark|Type|Flak|Pak|KwK|Sd\.?\s?Kfz\.?)\s?[IVX\d]+"
    # a name + Roman-numeral model: "Tiger I", "Panzer IV", "Churchill VII"
    r"|\b[A-Z][a-z]+\.?\s+(?:I{1,3}|IV|VI{0,3}|IX|X)\b"
    r"|\b(?:" + "|".join(sorted(_CLASS_ACRONYMS, key=len, reverse=True)) + r")"
    r"(?:\([A-Z]\))?(?=[\s,;.\-]|$)"
    r"|\b[A-Z]-boats?\b"
)
_CALIBER = re.compile(
    r"\b\d{1,3}(?:[.,]\d)?\s*-?\s*(?:mm|cm|in(?:ch)?|pdr|pounder)\b", re.I
)


def has_designation(name: str) -> bool:
    """True when the name carries a model/class designation (not a caliber or an ordinal)."""
    return bool(name) and _DESIGNATION.search(_CALIBER.sub(" ", name)) is not None


def is_bare_caliber(name: str) -> bool:
    """A caliber + class words with no model: "105-mm howitzer", "Russian 122-mm gun"."""
    return bool(name) and bool(_CALIBER.search(name)) and not has_designation(name)


def _is_generic_name(raw: str, generic_words) -> bool:
    """A bare caliber, a generic class word, or a ship-type word (singular or plural)."""
    norm = re.sub(r"[\s_]+", " ", raw.lower()).strip()
    singular = norm[:-1] if norm.endswith("s") else norm
    return (
        is_bare_caliber(raw)
        or norm in generic_words
        or singular in generic_words
        or singular in _SHIP_TYPE_WORDS
    )


def deterministic_scope(
    name: str,
    category: Optional[str],
    is_alias: bool,
    generic_words,
    subcategory: Optional[str] = None,
) -> Optional[str]:
    """SPECIFIC / GENERIC when decidable without Grok, else None (ask the canonical resolver).

    Order: alias nickname or designation -> specific; bare caliber, a generic class word or a
    ship-type word -> generic; a capitalised proper name under category "naval" -> a named vessel
    (specific, to be disambiguated by the caller)."""
    raw = (name or "").strip()
    if not raw:
        return GENERIC
    if is_alias or has_designation(raw):
        return SPECIFIC
    if _is_generic_name(raw, generic_words):
        return GENERIC
    return SPECIFIC if is_named_vessel(raw, category, subcategory) else None


_NAME_PARTICLES = ("de", "la", "von")


def _is_ship_type(raw: str, subcategory: Optional[str]) -> bool:
    """A ship-type word ("Minesweeper" capitalised at a sentence start), or the record's own
    subcategory."""
    norm = re.sub(r"[\s_-]+", " ", raw.lower()).strip()
    sub = re.sub(r"[\s_-]+", " ", (subcategory or "").lower()).strip()
    return norm.rstrip("s") in _SHIP_TYPE_WORDS or (bool(sub) and norm == sub)


def is_named_vessel(
    name: str, category: Optional[str], subcategory: Optional[str] = None
) -> bool:
    """A ship's own name ("Quincy", "Texas", "Augusta"): category naval, capitalised, no class
    designation, and not a ship-type word."""
    raw = (name or "").strip()
    if (category or "").lower() != "naval" or not raw:
        return False
    if has_designation(raw) or _is_ship_type(raw, subcategory):
        return False
    if raw.split()[-1].lower().rstrip("s") in _CLASS_NOUNS:
        return False
    return all(w[:1].isupper() for w in raw.split() if w.lower() not in _NAME_PARTICLES)


# A last word that makes a naval name a KIND of thing, not one ship ("Higgins boat",
# "Mulberry Harbour", "Landing Craft Vehicle").
_CLASS_NOUNS = {
    "boat",
    "craft",
    "harbour",
    "harbor",
    "vehicle",
    "barge",
    "raft",
    "net",
    "mine",
    "gun",
    "torpedo",
    "charge",
    "rocket",
    "launcher",
    "buoy",
    "pontoon",
    "ship",
    "vessel",
    "tank",
}

# Ship TYPES (never a ship's own name).
_SHIP_TYPE_WORDS = {
    "battleship",
    "cruiser",
    "heavy cruiser",
    "light cruiser",
    "destroyer",
    "destroyer escort",
    "frigate",
    "corvette",
    "sloop",
    "minesweeper",
    "minelayer",
    "submarine",
    "torpedo boat",
    "gunboat",
    "monitor",
    "transport",
    "troopship",
    "landing craft",
    "landing ship",
    "aircraft carrier",
    "escort carrier",
    "carrier",
    "trawler",
    "tug",
    "tanker",
    "freighter",
    "liberty ship",
    "patrol boat",
    "motor torpedo boat",
    "flotilla",
    "convoy",
    "warship",
    "ship",
    "vessel",
    "boat",
}


# --- named-vessel disambiguation ----------------------------------------------------------------

# Index-page prefixes by operating navy (ISO alpha-3 as stamped on mentions); unknown -> all.
_NAVY_PREFIXES = {
    "USA": ["USS"],
    "GBR": ["HMS"],
    "CAN": ["HMCS"],
    "AUS": ["HMAS"],
    "NZL": ["HMNZS"],
    "FRA": ["French ship"],
    "DEU": ["German ship", "German cruiser", "German battleship", "German destroyer"],
    "POL": ["ORP"],
    "NOR": ["HNoMS"],
    "NLD": ["HNLMS"],
}
_DEFAULT_PREFIXES = ["USS", "HMS", "HMCS", "French ship", "German ship", "ORP"]
_ALL_PREFIXES = sorted(
    {p for ps in _NAVY_PREFIXES.values() for p in ps} | set(_DEFAULT_PREFIXES)
)


def is_vessel_key(name: str) -> bool:
    """A named vessel's index key: an unresolved stub, or a ship article title under a navy
    prefix ("USS Quincy (CA-71)", "HMS Belfast (C35)", "German battleship Bismarck"). Such keys
    are matched exactly, never fuzzily."""
    text = name or ""
    if "(unresolved vessel" in text:
        return True
    return any(text.startswith(p + " ") for p in _ALL_PREFIXES)


_MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}
_DMY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")
_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_FIELD = re.compile(
    r"^[ \t]*\|[ \t]*(?:Ship[ \t]+)?([a-z ]{3,20}?)[ \t]*=[ \t]*(.*)$", re.I | re.M
)
_CAREER_SPLIT = re.compile(r"\{\{\s*Infobox ship career", re.I)
# A fate that ENDED this career (the ship sank, or left this navy: AK-10 was "Seized in 1917").
_END_FATE = re.compile(
    r"sunk|scuttled|lost|wrecked|destroyed|scrapped|broken up|captured|seized|"
    r"sold|transferred|interned|returned to|expended|surrendered",
    re.I,
)
# ...unless the fate says the ship came back ("Sunk 7 Dec 1941, raised, repaired, returned to
# service"): then the fate date is not an end of service.
_FATE_RECOVERED = re.compile(
    r"returned to service|recommissioned|raised and repaired|raised,\s*repaired|"
    r"refloated and repaired|repaired and returned",
    re.I,
)


def _month(word: str) -> Optional[int]:
    w = word.lower()
    return next((n for name, n in _MONTHS.items() if name.startswith(w[:3])), None)


def parse_date(value: str, end: bool = False) -> Optional[_dt.date]:
    """The first date in an infobox value ("9 June 1936", "December 15, 1943", "1974"). A
    year-only value is the year's first day (start) or last day (end)."""
    text = re.sub(r"\[\[|\]\]|\{\{[^}]*\}\}|<[^>]+>", " ", value or "")
    for rx, order in ((_DMY, "dmy"), (_MDY, "mdy")):
        m = rx.search(text)
        if m:
            d, mon, y = (
                (m.group(1), m.group(2), m.group(3))
                if order == "dmy"
                else (m.group(2), m.group(1), m.group(3))
            )
            month = _month(mon)
            if month:
                try:
                    return _dt.date(int(y), month, int(d))
                except ValueError:
                    pass
    m = _YEAR.search(text)
    if m:
        return (
            _dt.date(int(m.group(1)), 12, 31)
            if end
            else _dt.date(int(m.group(1)), 1, 1)
        )
    return None


_START_FIELDS = ("commissioned", "recommissioned", "acquired", "in service", "launched")
_END_FIELDS = ("decommissioned", "out of service", "struck")


def _first_date(fields: Dict[str, str], keys, end: bool = False) -> Optional[_dt.date]:
    for key in keys:
        found = parse_date(fields.get(key, ""), end=end)
        if found:
            return found
    return None


def _career_period(
    block: str,
) -> Optional[Tuple[Optional[_dt.date], Optional[_dt.date]]]:
    """(start, end) of one infobox career block, or None when it has no dates."""
    block = block.split("{{Infobox ship characteristics", 1)[0]
    fields: Dict[str, str] = {}
    for key, val in _FIELD.findall(block):
        fields.setdefault(key.strip().lower(), val.strip())
    start = _first_date(fields, _START_FIELDS)
    end = _first_date(fields, _END_FIELDS, end=True)
    fate = fields.get("fate", "")
    ended = _END_FATE.search(fate) and not _FATE_RECOVERED.search(fate)
    fate_end = parse_date(fate, end=True) if ended else None
    if fate_end and (end is None or fate_end < end):
        end = fate_end
    return (start, end) if (start or end) else None


def service_periods(
    wikitext: str,
) -> List[Tuple[Optional[_dt.date], Optional[_dt.date]]]:
    """(start, end) per career block of a ship article's infobox. start = commissioned (or
    recommissioned / acquired / launched); end = decommissioned, struck, or a fate that ended the
    career (sunk, scrapped, seized, ...). None = unknown."""
    blocks = _CAREER_SPLIT.split(wikitext or "")[1:] or [wikitext or ""]
    return [p for p in (_career_period(b) for b in blocks) if p]


def in_service(periods, on: _dt.date) -> Optional[bool]:
    """True/False when the dates decide it; None when no period has a usable start."""
    known = [p for p in periods if p[0]]
    if not known:
        return None
    return any(s <= on and (e is None or on <= e) for s, e in known)


def ship_class(wikitext: str) -> str:
    m = re.search(
        r"^[ \t]*\|[ \t]*(?:Ship[ \t]+)?class[ \t]*=[ \t]*(.*)$",
        wikitext or "",
        re.I | re.M,
    )
    return (
        re.sub(r"\{\{|\}\}|\[\[|\]\]|\|", " ", m.group(1)).strip().lower() if m else ""
    )


def mention_date(mention: Dict[str, Any]) -> Optional[_dt.date]:
    """The mention's date (Phase 1/2 date_start, ISO or free text), or None."""
    raw = str((mention or {}).get("date") or "").strip()
    if not raw:
        return None
    try:
        return _dt.date.fromisoformat(raw[:10])
    except ValueError:
        return parse_date(raw)


# Base ship types a stated subcategory can be checked against an infobox class with. Vague words
# (ship, vessel, warship, transport) say nothing about the class and are not checked.
_BASE_SHIP_TYPES = {
    "battleship",
    "cruiser",
    "destroyer",
    "destroyer escort",
    "frigate",
    "corvette",
    "sloop",
    "minesweeper",
    "minelayer",
    "submarine",
    "gunboat",
    "monitor",
    "aircraft carrier",
    "escort carrier",
    "carrier",
    "trawler",
    "tug",
    "tanker",
}


def _ship_kind(subcategory: Optional[str]) -> str:
    """The checkable ship type in a subcategory ("heavy_cruiser" -> "cruiser", "destroyer_escort"
    -> "destroyer escort"), or "" when it names none."""
    phrase = " ".join((subcategory or "").replace("_", " ").lower().split())
    if phrase in _BASE_SHIP_TYPES:
        return phrase
    last = phrase.split()[-1] if phrase else ""
    return last if last in _BASE_SHIP_TYPES else ""


def _class_matches(kind: str, ship_class_text: str) -> bool:
    if kind == "carrier":  # "aircraft carrier" / "escort carrier" are both carriers
        return "carrier" in ship_class_text
    if kind == "destroyer":  # a destroyer escort is not a destroyer
        return (
            "destroyer" in ship_class_text and "destroyer escort" not in ship_class_text
        )
    return kind in ship_class_text


def filter_candidates(
    candidates: List[Dict[str, Any]], on: Optional[_dt.date], subcategory: Optional[str]
) -> List[Dict[str, Any]]:
    """Keep candidates not known to be out of service on the date, and not known to be of a
    different type than the stated one (an unknown class is kept: no evidence against it).
    """
    kept = [c for c in candidates if not on or c.get("in_service") is not False]
    kind = _ship_kind(subcategory)
    if not kind:
        return kept
    return [c for c in kept if not c.get("class") or _class_matches(kind, c["class"])]


def candidate_record(
    title: str, wikitext: str, on: Optional[_dt.date]
) -> Dict[str, Any]:
    periods = service_periods(wikitext)
    return {
        "title": title,
        "class": ship_class(wikitext),
        "service": [
            [s.isoformat() if s else None, e.isoformat() if e else None]
            for s, e in periods
        ],
        "in_service": in_service(periods, on) if on else None,
    }


def _with_service_on(
    candidate: Dict[str, Any], on: Optional[_dt.date]
) -> Dict[str, Any]:
    """A cached candidate (title/class/service) with ``in_service`` evaluated for ``on``."""
    periods = [
        (
            _dt.date.fromisoformat(s) if s else None,
            _dt.date.fromisoformat(e) if e else None,
        )
        for s, e in candidate.get("service") or []
    ]
    return {**candidate, "in_service": in_service(periods, on) if on else None}


def _wiki_get(params: Dict[str, Any], subject: str) -> Optional[Dict[str, Any]]:
    """One Wikipedia API call (shared Wikimedia client: back-off, provenance). The JSON, or None
    when Wikipedia could not be reached (transient - never cached as a miss)."""
    from src.utils.wikimedia_client import TRANSIENT, wikimedia_get

    resp = wikimedia_get(
        "wikipedia",
        "https://en.wikipedia.org/w/api.php",
        params={"format": "json", **params},
        headers={"User-Agent": "SecondWorldWarAsData/1.0 (ship disambiguation)"},
        timeout=15,
        subject=subject,
        result_summary_fn=lambda r: f"vessel status={r.status_code}",
    )
    if resp is TRANSIENT or getattr(resp, "status_code", 0) != 200:
        return None
    try:
        data = resp.json()  # type: ignore[union-attr]
    except ValueError:
        return None
    if not isinstance(data, dict) or "error" in data:
        if isinstance(data, dict):
            logger.info(
                "OBSERV equipment.vessel_wiki_error subject=%r code=%s (treated as transient)",
                subject,
                (data.get("error") or {}).get("code"),
            )
        return None
    return data


def _page(data: Dict[str, Any]) -> Dict[str, Any]:
    pages = (data.get("query") or {}).get("pages") or {}
    return next(iter(pages.values()), {}) if pages else {}


def _wikitext(page: Dict[str, Any]) -> str:
    revs = page.get("revisions") or [{}]
    slot = (revs[0].get("slots") or {}).get("main") or {}
    return slot.get("*") or revs[0].get("*") or ""


def _cached_candidates(key: str) -> Optional[List[Dict[str, Any]]]:
    """The cached date-free candidate list ([] for a cached miss), or None when not cached."""
    import json as _json

    from src.utils.search_cache import get_cached

    cached = get_cached("vessel_candidates", key)
    if cached == "NOT_FOUND":
        return []
    if not cached:
        return None
    try:
        data = _json.loads(cached)
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def wikipedia_candidates(
    name: str, navy: Optional[str], on: Optional[_dt.date]
) -> Optional[List[Dict[str, Any]]]:
    """Candidate ships named ``name`` from Wikipedia ship index pages ("USS Quincy" lists every
    USS Quincy), each with class + service periods + ``in_service`` on ``on``. A prefixed title
    that is itself one ship's article is a single candidate. The date-free candidate list is
    cached per (navy, name). None = Wikipedia unreachable (transient, not cached)."""
    import json as _json

    from src.utils.search_cache import cache_result

    prefixes = _NAVY_PREFIXES.get((navy or "").upper(), _DEFAULT_PREFIXES)
    key = f"{'|'.join(prefixes)}::{name}"
    cached = _cached_candidates(key)
    if cached is not None:
        try:
            return [_with_service_on(c, on) for c in cached]
        except (ValueError, TypeError, AttributeError):
            logger.info(
                "OBSERV equipment.vessel_cache_corrupt key=%r (refetching)", key
            )
    candidates = _fetch_candidates(name, prefixes)
    if candidates is None:
        return None
    cache_result(
        "vessel_candidates", key, _json.dumps(candidates) if candidates else None
    )
    return [_with_service_on(c, on) for c in candidates]


_MAX_LINK_PAGES = 5


def _query(title: str, with_links: bool) -> Optional[Dict[str, Any]]:
    params = {
        "action": "query",
        "titles": title,
        "prop": "links|revisions" if with_links else "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "redirects": "1",
    }
    if not with_links:
        return _wiki_get(params, title)
    params["pllimit"] = "max"
    data = _wiki_get(params, title)
    return _with_all_links(data, params, title) if data is not None else None


def _with_all_links(
    data: Dict[str, Any], params: Dict[str, Any], title: str
) -> Optional[Dict[str, Any]]:
    """Follow plcontinue so the page carries ALL its links (navboxes can exceed one batch).
    None = transient or still incomplete after _MAX_LINK_PAGES (never cache a truncated list).
    """
    for _ in range(_MAX_LINK_PAGES):
        cont = data.get("continue") or {}
        if not cont.get("plcontinue"):
            return data
        more = _wiki_get({**params, "plcontinue": cont["plcontinue"]}, title)
        if more is None:
            return None
        _page(data).setdefault("links", []).extend(_page(more).get("links") or [])
        data = {**data, "continue": more.get("continue")}
    return None if (data.get("continue") or {}).get("plcontinue") else data


def _index_titles(name: str, prefix: str) -> Optional[List[str]]:
    """The ship articles a "<prefix> <name>" index page lists (or the page itself when it is one
    ship's article). None = transient."""
    index_title = f"{prefix} {name}"
    data = _query(index_title, with_links=True)
    if data is None:
        return None
    page = _page(data)
    if not page or "missing" in page:
        return []
    resolved = page.get("title", index_title)
    linked = [
        link["title"]
        for link in page.get("links") or []
        if link.get("title", "").startswith(f"{resolved} (")
    ]
    if linked:
        return linked
    return [resolved] if service_periods(_wikitext(page)) else []


def _fetch_candidates(name: str, prefixes: List[str]) -> Optional[List[Dict[str, Any]]]:
    """Date-free candidates (title, class, service) from the index pages. None = transient."""
    titles: List[str] = []
    for prefix in prefixes:
        found = _index_titles(name, prefix)
        if found is None:
            return None
        titles.extend(found)
    candidates = []
    for title in dict.fromkeys(titles):
        data = _query(title, with_links=False)
        if data is None:
            return None
        page = _page(data)
        if "missing" in page:
            continue  # a red link on the index page: no such article
        if not page.get("revisions"):
            return (
                None  # listed but unreadable now: transient, never cache a partial list
            )
        candidates.append(candidate_record(title, _wikitext(page), None))
    return candidates


def _choice_prompt(name, candidates, paragraph, on) -> str:
    from src.utils.prompt_loader import render_prompt

    listing = "\n".join(
        f"- {c['title']} | class: {c.get('class') or 'unknown'} | service: {c.get('service')}"
        for c in candidates
    )
    return render_prompt(
        "equipment_vessel_choice",
        vessel_name=name,
        date=on.isoformat() if on else "unknown",
        paragraph=(paragraph or "")[:2000],
        candidates=listing,
    )


def _ask_grok(prompt: str, name: str, grok_client) -> Optional[Dict[str, Any]]:
    from src.grok_client import BatchModeCollecting

    try:
        result = grok_client.extract_json(
            prompt,
            temperature=0.0,
            use_cache=True,
            cache_type="equipment_disambiguation",
        )
    except BatchModeCollecting:
        raise
    except Exception as e:  # noqa: BLE001 - unresolved is the safe outcome
        logger.warning("OBSERV equipment.vessel_choice_failed name=%r err=%s", name, e)
        return None
    return result if isinstance(result, dict) else None


def _confidence(result: Dict[str, Any]) -> float:
    try:
        return float(result.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _grok_choose(
    name: str, candidates: List[Dict[str, Any]], paragraph: str, on, grok_client
) -> Tuple[Optional[str], float]:
    """Grok picks ONE of the listed candidates for the paragraph, or none. Never invents a ship."""
    if not grok_client or not candidates:
        return None, 0.0
    result = _ask_grok(
        _choice_prompt(name, candidates, paragraph, on), name, grok_client
    )
    if result is None:
        return None, 0.0
    choice = result.get("title")
    titles = {c["title"] for c in candidates}
    return (choice if choice in titles else None), _confidence(result)


VESSEL_MIN_CONFIDENCE = 0.8


def _empty_resolution(on: Optional[_dt.date]) -> Dict[str, Any]:
    return {
        "resolved_title": None,
        "method": None,
        "candidates": [],
        "date_used": on.isoformat() if on else None,
        "date_source": "mention" if on else None,
        "confidence": None,
    }


def resolve_vessel(
    name: str,
    mention: Dict[str, Any],
    subcategory: Optional[str],
    grok_client=None,
) -> Dict[str, Any]:
    """Resolve a named vessel to ONE Wikipedia ship article. Returns an ``identity_resolution``
    dict: resolved_title (None when unresolved), method, candidates, date_used, date_source,
    confidence, plus ``transient`` when Wikipedia could not be reached (retry later)."""
    mention = mention or {}
    on = mention_date(mention)
    out = _empty_resolution(on)
    candidates = wikipedia_candidates(name, mention.get("operating_country"), on)
    if candidates is None:
        out["transient"] = True
        return out
    out["candidates"] = candidates
    kept = filter_candidates(candidates, on, subcategory)
    if len(kept) == 1 and on and kept[0].get("in_service") is True:
        out.update(
            resolved_title=kept[0]["title"], method="service_dates", confidence=1.0
        )
        return out
    choice, confidence = _grok_choose(
        name, kept, mention.get("original_text", ""), on, grok_client
    )
    out["confidence"] = confidence or None
    if choice and confidence >= VESSEL_MIN_CONFIDENCE:
        out.update(resolved_title=choice, method="grok_choice")
    return out
