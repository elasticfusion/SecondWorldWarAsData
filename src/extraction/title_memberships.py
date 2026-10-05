"""Derive civilian group memberships from a person's title/aliases (Phase-3 enrichment).

A civilian title implies (often multiple, concurrent) group memberships:
"Representative John Smith from NJ, a Republican" -> House of Representatives + New
Jersey + Republican Party.

CORRECTNESS GUARD (owner-confirmed, built first): a title/honorific is POINT-IN-TIME.
A later honorific does NOT prove the person held that role in the source's timeframe,
so every title-derived membership is born **date_verified=false, implied_from_title=
true, as_of_source_date=<source date>** and must NOT be asserted as fact until a
DELIBERATE temporal-validation step (``validate_membership_dates``) confirms it. That
step asks Grok "was <person> a member of <group> in <source year>?" and flips
date_verified. Opt-in + fail-safe; never fabricates.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Title/honorific -> (group name, group_kind). Legislative/executive/judiciary.
_TITLE_GROUPS = [
    (
        r"\brepresentative\b|\bcongress(?:wo)?man\b|\brep\.\b",
        "House of Representatives",
        "legislature",
    ),
    (r"\bsenator\b|\bsen\.\b", "Senate", "legislature"),
    (r"\bgovernor\b|\bgov\.\b", "", "executive"),  # group filled from the state
    (r"\bpresident\b", "Executive Office of the President", "executive"),
    (r"\bsecretary of (state|the treasury|war|defense|the navy)\b", "", "executive"),
    (r"\bjustice\b|\bchief justice\b", "Supreme Court", "judiciary"),
]

_US_STATES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
}
_STATE_ABBR = {
    "nj": "New Jersey",
    "ny": "New York",
    "ca": "California",
    "tx": "Texas",
    "pa": "Pennsylvania",
    "ok": "Oklahoma",
    "ma": "Massachusetts",
}

_PARTIES = {
    "republican": "Republican Party",
    "democrat": "Democratic Party",
    "democratic": "Democratic Party",
}


def _text_blob(person: Dict) -> str:
    bp = person.get("biographical_profile") or {}
    parts = [person.get("name", ""), bp.get("biographical_details", "") or ""]
    parts += [str(a) for a in (bp.get("aliases") or [])]
    return " ".join(parts).lower()


def _source_year(person: Dict) -> Optional[str]:
    for m in person.get("event_mentions", []) or []:
        d = m.get("date") or ""
        mm = re.search(r"(19\d\d)", str(d))
        if mm:
            return mm.group(1)
    return None


def derive_title_memberships(person: Dict) -> List[Dict[str, Any]]:
    """Parse the person's title/aliases into civilian group memberships, each born
    date-unverified (implied_from_title=true). Returns a list of GroupAffiliation dicts.
    Does NOT assert anything as fact — these must be temporally validated."""
    blob = _text_blob(person)
    as_of = _source_year(person)
    out: List[Dict[str, Any]] = []

    def add(group: str, kind: str, source_title: str):
        if not group:
            return
        if any(a.get("group", "").lower() == group.lower() for a in out):
            return
        out.append(
            {
                "group": group,
                "group_kind": kind,
                "implied_from_title": True,
                "date_verified": False,  # correctness guard: unverified until checked
                "as_of_source_date": as_of,
                "source_title": source_title.strip(),
            }
        )

    # Title-based groups
    for pattern, group, kind in _TITLE_GROUPS:
        m = re.search(pattern, blob)
        if m:
            add(group, kind, m.group(0))
    # State ("from New Jersey" / "(NJ)")
    for st in _US_STATES:
        if re.search(rf"\b{re.escape(st)}\b", blob):
            add(st.title(), "state", st)
            break
    else:
        for abbr, full in _STATE_ABBR.items():
            if re.search(rf"\b{abbr}\b", blob):
                add(full, "state", abbr)
                break
    # Party
    for key, party in _PARTIES.items():
        if re.search(rf"\b{key}\b", blob):
            add(party, "party", key)
            break
    return out


def enrich_title_memberships(person: Dict) -> int:
    """Attach title-derived memberships to biographical_profile.group_affiliations,
    skipping any group already present. Returns the count added. Born date-unverified.
    Fail-safe."""
    try:
        bp = person.setdefault("biographical_profile", {})
        existing = bp.setdefault("group_affiliations", [])
        existing_names = {
            a.get("group", "").lower() for a in existing if isinstance(a, dict)
        }
        added = 0
        for aff in derive_title_memberships(person):
            if aff["group"].lower() not in existing_names:
                existing.append(aff)
                added += 1
        return added
    except Exception as e:  # noqa: BLE001 - enrichment extra, never block the person
        logger.warning("title-membership enrichment skipped: %s", e)
        return 0


def validate_membership_dates(person: Dict, grok_client: Any) -> int:
    """DELIBERATE temporal-validation step: for each date-unverified title membership,
    ask Grok whether the person held that membership in the source timeframe, and flip
    date_verified. Never asserts without confirmation. Returns count validated true.

    Gated by the caller (opt-in). Fail-safe: a Grok error leaves date_verified False.
    """
    bp = person.get("biographical_profile") or {}
    name = person.get("name") or person.get("current_name") or ""
    affs = bp.get("group_affiliations") or []
    verified = 0
    for aff in affs:
        if not isinstance(aff, dict) or aff.get("date_verified"):
            continue
        if not aff.get("implied_from_title"):
            continue
        year = aff.get("as_of_source_date")
        group = aff.get("group")
        if not (name and group and year):
            continue
        try:
            answer = _ask_membership(grok_client, name, group, year)
        except Exception as e:  # noqa: BLE001 - leave unverified on error
            logger.warning("membership temporal-validation skipped (%s): %s", group, e)
            continue
        if answer is True:
            aff["date_verified"] = True
            verified += 1
        elif answer is False:
            aff["date_verified"] = (
                False  # explicit negative; keep flagged, not asserted
            )
    return verified


def _ask_membership(
    grok_client: Any, name: str, group: str, year: str
) -> Optional[bool]:
    """Ask Grok a yes/no temporal-membership question. Returns True/False/None(unsure)."""
    prompt = (
        f"Historical fact check. Was {name} a member of {group} in {year}? "
        f"Answer with exactly one word: YES, NO, or UNKNOWN."
    )
    resp = grok_client.chat_completion(prompt).strip().upper()
    if resp.startswith("YES"):
        return True
    if resp.startswith("NO"):
        return False
    return None
