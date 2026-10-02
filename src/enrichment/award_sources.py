"""Authoritative award-citation sourcing (pluggable).

Goal: for a person who appears **in the context of an award**, obtain the award's
verbatim CITATION TEXT from an **authoritative source** (not a search engine) and
record a full source record (citation_text, source_name, source_url,
retrieved_date, verified) on the matching ``MilitaryAward``.

Design
------
- Sources are pluggable behind :class:`AwardCitationSource`. Two kinds share the
  same interface: a **direct-fetch** source (polite, rate-limited, cached HTTP
  against an authoritative site that serves our IP) and an **offline/bulk-dataset**
  source (query a locally-held recipients+citations dataset — zero live scraping).
  valor.defense.gov is Akamai-WAF-blocked at the IP level from datacenter egress
  (verified), so it is NOT a viable direct source here; see AWARD_SOURCING.md.
- Gating: lookups run ONLY for ``nationality == USA`` people who already have at
  least one award (the "award context"). Other nationalities are a future
  extension (their own sources), so the gate is explicit + overridable.
- Verification, not fabrication: a citation is accepted only when the source
  record's recipient matches this person + award. Unmatched → not written.

This module is network-agnostic and import-safe; concrete sources live in sibling
modules and register themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Protocol

logger = logging.getLogger(__name__)

# Nationalities this module currently has authoritative sources for. US-only now;
# extend as other countries' award sources are added (per owner: other countries
# may follow). Values are matched case-insensitively against common spellings.
_US_NATIONALITY = {"usa", "us", "united states", "united states of america", "american"}


@dataclass
class AwardCitation:
    """A citation obtained from an authoritative source, with provenance."""

    citation_text: str
    source_name: str
    source_url: str
    retrieved_date: str  # ISO-8601
    award: str = ""  # the award this citation is for, if the source specifies
    verified: bool = False


class AwardCitationSource(Protocol):
    """An authoritative award-citation source (direct-fetch OR offline dataset).

    Implementations must be POLITE (rate-limited + cached for direct sources) and
    return only citations whose recipient genuinely matches ``name`` — never a
    best-guess. Return [] on no-match; raise only on a true transient error (the
    caller treats that as 'try next source', not a durable negative)."""

    name: str

    def lookup(self, person_name: str, award_hint: str = "") -> List[AwardCitation]: ...


def is_us_person(nationality: Optional[str]) -> bool:
    """True if the nationality denotes the United States (case/spelling tolerant)."""
    return bool(nationality) and nationality.strip().lower() in _US_NATIONALITY


def has_award_context(person: dict) -> bool:
    """True if the person appears in the context of an award (has >=1 award)."""
    bp = person.get("biographical_profile") or {}
    awards = bp.get("military_awards") or person.get("military_awards") or []
    return bool(awards)


def should_source_awards(person: dict) -> bool:
    """Gate: only US personnel named in an award context (for now)."""
    bp = person.get("biographical_profile") or {}
    nat = bp.get("nationality") or person.get("nationality")
    return is_us_person(nat) and has_award_context(person)


def enrich_person_awards(
    person: dict,
    sources: List[AwardCitationSource],
) -> int:
    """Attach authoritative citations to this person's awards via ``sources`` in
    order (first authoritative match wins per award). Returns the number of awards
    augmented with a citation. Fail-safe: a source error is logged and the next
    source is tried; never raises.

    Only fills a citation on an award that lacks one; existing provenance is kept.
    """
    if not should_source_awards(person):
        return 0
    bp = person.setdefault("biographical_profile", {})
    awards = bp.get("military_awards") or person.get("military_awards") or []
    name = person.get("name") or person.get("current_name") or ""
    if not name or not awards:
        return 0

    filled = 0
    for award in awards:
        if not isinstance(award, dict) or award.get("citation_text"):
            continue  # keep existing provenance; only fill gaps
        award_name = award.get("award", "")
        citation = _first_match(sources, name, award_name)
        if citation:
            award["citation_text"] = citation.citation_text
            award["source_name"] = citation.source_name
            award["source_url"] = citation.source_url
            award["retrieved_date"] = citation.retrieved_date
            award["verified"] = citation.verified
            filled += 1
            logger.info(
                "Award citation for %s (%s) from %s",
                name,
                award_name or "?",
                citation.source_name,
            )
    return filled


def _first_match(
    sources: List[AwardCitationSource], name: str, award_hint: str
) -> Optional[AwardCitation]:
    for src in sources:
        try:
            results = src.lookup(name, award_hint)
        except Exception as e:  # noqa: BLE001 - try next source; not a durable fail
            logger.warning("Award source '%s' errored for %s: %s", src.name, name, e)
            continue
        for c in results:
            # If the award matches (or the source didn't specify), accept the
            # first verified citation.
            if c.verified and (not award_hint or not c.award or c.award == award_hint):
                return c
    return None


def today_iso() -> str:
    return date.today().isoformat()
