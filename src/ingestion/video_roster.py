"""Tiered candidate-roster assembly for video speaker-id (9c).

Builds the list of :class:`RosterCandidate` (from ``speaker_id``) that is offered
to perception (9b) as a SUGGESTION prior — never a closed set. Sources, best
(most authoritative) first, each tagged with its trust level so the resolver
(9a) weights a match against a higher-trust entry more:

1. **IMDB / Wikipedia episode cast/appearances** — for an identifiable published
   work (e.g. "The World at War" S01E17), the curated cast/interviewee list is
   the strongest roster. Named, with reference images. May be absent (a raw
   clip has no entry) — opportunistic, like the OpenSERP/Wikipedia enrichers.
2. **Transcript-extracted People** — who is *named* in this video's own audio;
   always available once the transcript exists.
3. **Topic/campaign roster** — known ETO figures for the subject; widening
   fallback so a speaker mentioned before formal extraction still has a prior.

De-duplicated by PersonID, keeping the HIGHEST-trust source per person and
merging reference-image URLs. External lookups (IMDB/Wikipedia) are INJECTED so
this module is pure + unit-testable; a live Wikipedia impl is provided.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Protocol, runtime_checkable

from src.ingestion.speaker_id import RosterCandidate, RosterSource

logger = logging.getLogger(__name__)

# Trust order (higher index = more authoritative) for de-dup precedence.
_SOURCE_RANK = {
    RosterSource.IMDB_CAST: 4,
    RosterSource.WIKIPEDIA: 3,
    RosterSource.TRANSCRIPT: 2,
    RosterSource.TOPIC: 1,
}


@runtime_checkable
class AppearanceLookup(Protocol):
    """Look up who appears in an identifiable published video (IMDB/Wikipedia)."""

    def lookup(self, title: str) -> List[dict]:
        """Return [{person_id?, name, image_urls?, source}] for the title, or []
        when the work isn't identifiable / has no cast list."""


def _merge(into: Dict[str, RosterCandidate], cand: RosterCandidate) -> None:
    """Insert/merge a candidate by PersonID, keeping the highest-trust source and
    unioning reference images."""
    cur = into.get(cand.person_id)
    if cur is None:
        into[cand.person_id] = cand
        return
    # keep higher-trust source; union images
    imgs = list(dict.fromkeys([*cur.reference_image_urls, *cand.reference_image_urls]))
    if _SOURCE_RANK[cand.source] > _SOURCE_RANK[cur.source]:
        cur = RosterCandidate(cand.person_id, cand.name, cand.source, imgs)
    else:
        cur.reference_image_urls = imgs
    into[cand.person_id] = cur


def build_roster(
    *,
    title: Optional[str] = None,
    transcript_people: Optional[List[dict]] = None,
    topic_people: Optional[List[dict]] = None,
    appearance_lookup: Optional[AppearanceLookup] = None,
) -> List[RosterCandidate]:
    """Assemble the tiered candidate roster (see module docstring).

    transcript_people / topic_people: [{person_id, name, image_urls?}] from the
    project's People entities (transcript-derived resp. topic/campaign spine).
    appearance_lookup: optional IMDB/Wikipedia cast lookup (title -> appearances).
    """
    roster: Dict[str, RosterCandidate] = {}

    # 1) IMDB/Wikipedia episode cast (strongest), if the work is identifiable.
    if title and appearance_lookup is not None:
        try:
            for a in appearance_lookup.lookup(title) or []:
                pid = a.get("person_id") or f"name::{a.get('name', '').strip()}"
                name = a.get("name", "").strip()
                if not name:
                    continue
                src = (
                    RosterSource.IMDB_CAST
                    if a.get("source") == "imdb"
                    else RosterSource.WIKIPEDIA
                )
                _merge(
                    roster,
                    RosterCandidate(
                        pid, name, src, list(a.get("image_urls", []) or [])
                    ),
                )
        except Exception as e:  # pragma: no cover - lookups are best-effort
            logger.warning("Appearance lookup failed for %r: %s", title, e)

    # 2) transcript-extracted People (always available).
    for p in transcript_people or []:
        pid, name = p.get("person_id"), (p.get("name") or "").strip()
        if pid and name:
            _merge(
                roster,
                RosterCandidate(
                    pid,
                    name,
                    RosterSource.TRANSCRIPT,
                    list(p.get("image_urls", []) or []),
                ),
            )

    # 3) topic/campaign roster (widening fallback).
    for p in topic_people or []:
        pid, name = p.get("person_id"), (p.get("name") or "").strip()
        if pid and name:
            _merge(
                roster,
                RosterCandidate(
                    pid, name, RosterSource.TOPIC, list(p.get("image_urls", []) or [])
                ),
            )

    return list(roster.values())


# --- live Wikipedia appearance lookup (opportunistic) ------------------------


class WikipediaAppearanceLookup:
    """Best-effort episode/film appearance lookup via Wikipedia. Returns [] when
    the work isn't found (a raw clip) — the roster then falls back to transcript
    + topic tiers. Reuses the enrichment layer's Wikipedia conventions."""

    def lookup(self, title: str) -> List[dict]:
        try:
            from src.enrichment.situational_geocode import wikipedia_context

            extract = wikipedia_context(title, session=None)
        except Exception as e:  # pragma: no cover - network/best-effort
            logger.warning("Wikipedia appearance lookup error for %r: %s", title, e)
            return []
        if not extract:
            return []
        # Extract "Featuring/Interviews with <Name>" style names conservatively;
        # a wrong/empty parse just yields a weaker roster (transcript tier covers).
        return _names_from_extract(extract)


def _names_from_extract(extract: str) -> List[dict]:
    """Very conservative name harvest from a Wikipedia extract — Capitalized
    multi-word tokens near appearance cues. Names still resolve to PersonIDs
    downstream; this only seeds the *suggestion* roster."""
    import re

    names: List[dict] = []
    seen: set = set()
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z.]+){1,3})\b", extract):
        name = m.group(1).strip()
        if len(name) < 5 or name in seen:
            continue
        seen.add(name)
        names.append({"name": name, "source": "wikipedia", "image_urls": []})
    return names[:40]  # cap — a suggestion, not exhaustive
