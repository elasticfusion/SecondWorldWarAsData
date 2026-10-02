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

# Nationality spellings -> canonical ISO-ish code used in the registry's
# `nationality` gate. Extend as new countries are added.
_NATIONALITY_ALIASES = {
    "usa": "USA",
    "us": "USA",
    "united states": "USA",
    "united states of america": "USA",
    "american": "USA",
    "gbr": "GBR",
    "uk": "GBR",
    "united kingdom": "GBR",
    "british": "GBR",
    "england": "GBR",
    "english": "GBR",
    "scotland": "GBR",
    "wales": "GBR",
    "deu": "DEU",
    "germany": "DEU",
    "german": "DEU",
    "fra": "FRA",
    "france": "FRA",
    "french": "FRA",
    "can": "CAN",
    "canada": "CAN",
    "canadian": "CAN",
    "ita": "ITA",
    "italy": "ITA",
    "italian": "ITA",
}


def canonical_nationality(nationality: Optional[str]) -> Optional[str]:
    """Map a free-text nationality to the registry's canonical code (USA/GBR/...)."""
    if not nationality:
        return None
    return _NATIONALITY_ALIASES.get(nationality.strip().lower())


@dataclass
class AwardCitation:
    """A citation obtained from an authoritative source, with provenance."""

    citation_text: str
    source_name: str
    source_url: str
    retrieved_date: str  # ISO-8601
    award: str = ""  # the award this citation is for, if the source specifies
    verified: bool = False
    language: str = "English"  # source language; non-English is translated on attach


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
    return canonical_nationality(nationality) == "USA"


def has_award_context(person: dict) -> bool:
    """True if the person appears in the context of an award (has >=1 award)."""
    bp = person.get("biographical_profile") or {}
    awards = bp.get("military_awards") or person.get("military_awards") or []
    return bool(awards)


def person_nationality(person: dict) -> Optional[str]:
    """Canonical nationality code for a person record, or None."""
    bp = person.get("biographical_profile") or {}
    return canonical_nationality(bp.get("nationality") or person.get("nationality"))


def should_source_awards(person: dict) -> bool:
    """Gate: a person named in an award context whose nationality is one we have a
    registered source for. (Nationality-agnostic now — any registered country.)"""
    return bool(person_nationality(person)) and has_award_context(person)


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
            text, original, language = _normalize_citation_language(citation)
            award["citation_text"] = text
            if original is not None:
                award["citation_text_original"] = original
                award["citation_language"] = language
            award["source_name"] = citation.source_name
            award["source_url"] = citation.source_url
            award["retrieved_date"] = citation.retrieved_date
            award["verified"] = citation.verified
            filled += 1
            logger.info(
                "Award citation for %s (%s) from %s%s",
                name,
                award_name or "?",
                citation.source_name,
                f" [translated from {language}]" if original is not None else "",
            )
    return filled


def _normalize_citation_language(citation: "AwardCitation"):
    """Return (english_text, original_text_or_None, language).

    English citations pass through (original=None). A non-English citation is
    translated to English via the shared translation module (same pattern as the
    Phase-0 translate-at-the-seam decision); the verbatim original is preserved
    for provenance. Fail-safe: on disabled/error, keep the original text as-is and
    still record its language.
    """
    lang = citation.language or "English"
    if lang.strip().lower() in ("english", "en", ""):
        return citation.citation_text, None, "English"
    try:
        from src.ingestion.translation import normalize_markdown_to_english

        english = normalize_markdown_to_english(citation.citation_text, per_page=False)
        if (
            english
            and english.strip()
            and english.strip() != citation.citation_text.strip()
        ):
            return english, citation.citation_text, lang
    except Exception as e:  # noqa: BLE001 - fail-safe: keep original
        logger.warning("Award citation translation skipped (%s): %s", lang, e)
    # Disabled / unchanged / error → keep original but record language for provenance.
    return citation.citation_text, citation.citation_text, lang


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
