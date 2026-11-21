"""The ONE way a Phase 3 step queues a URL it found for human review (Group 3, owner 2026-11-17).

Rule (owner): a URL a step finds (an OpenSERP page, a link in a parsed document, an Archive.org or
Gutenberg copy, a NARA record, an LLM-suggested URL) is shown to a human only after Grok has judged
it relevant; a human approves it; approval submits it to Phase 0 (``fetch_review.submit_affirmed_batch``).
A find is held in the queue ONLY: no entity record is written until it is approved.

``enqueue_find`` is the judge-then-enqueue step every finder calls:

* it refuses a find without a positive Grok verdict (``RELEVANT``): no link is ever shown to a human
  unjudged;
* it records the find once per (provider, origin, URL): a later run does not queue it again, and a
  find a human rejected or already submitted never comes back (the event, with its review status,
  is the durable record: ``fetch_provenance.record_fetch_event(once_key=...)``);
* it carries the origin (entity type and ULID, or the source document) and the query, so the source
  an approved find becomes can be linked back to what asked for it.

Search calls are NOT finds: ``fetch_provenance.recorded_fetch`` records them as provenance only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.utils import fetch_provenance as fp

logger = logging.getLogger(__name__)

RELEVANT = "relevant"  # Grok said YES: the only verdict that reaches a human

ENQUEUED = "enqueued"
ALREADY_QUEUED = "already_queued"
REFUSED = "refused"


@dataclass(frozen=True)
class Origin:
    """What asked for the find: an entity (``entity_type`` + its ULID) or a source document."""

    entity_type: str = (
        ""  # person | people_group | place | source_section | bibliography | book
    )
    entity_id: str = (
        ""  # PersonID / PeopleGroupID / PlaceID / SourceSectionID / BibliographyID
    )
    book: str = ""  # the source document's slug, for a link found in a document
    source_url: str = ""  # the document's own URL
    query: str = ""  # the search that found it

    @property
    def key(self) -> str:
        """The part of the find's identity that says WHO asked: the entity ULID, else the document
        (and the section/URL it was found in)."""
        return self.entity_id or f"{self.book}|{self.source_url}"


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: Any, limit: int) -> str:
    """Untrusted text for a stored field: no control characters, no ``->`` (the review reads the
    acquisition URL after the first ``->`` of a summary), capped."""
    return _CTRL.sub(" ", str(value or "")).replace("->", "- >")[:limit]


def already_queued(
    provider: str, url: str, origin: "Origin", legacy_key: Optional[tuple] = None
) -> bool:
    """Whether this find is already recorded (any review status), so no Grok request is spent on it.
    ``legacy_key``: the once_key an older release used for the same find, so a find queued then
    (rejected or submitted too) is not queued again under the new id."""
    from src.utils.url_hygiene import clean_url

    clean = clean_url(url) or ""
    if clean and fp.event_recorded(provider, (origin.key, clean)):
        return True
    return bool(legacy_key and fp.event_recorded(provider, legacy_key))


def _refusal(provider: str, verdict: str, url: str) -> Optional[str]:
    """The cleaned URL when this find may be queued, else None (said when it is a code error)."""
    if provider not in fp.FIND_PROVIDERS:
        logger.error(
            "OBSERV find_queue.bad_provider provider=%r status=error - not a find provider: "
            "add it to fetch_provenance.FIND_PROVIDERS",
            provider[:40],
        )
        return None
    if verdict != RELEVANT:
        return None  # nobody is shown a link Grok has not called relevant
    from src.utils.url_hygiene import clean_url

    return clean_url(url) or None


def _event_params(
    verdict: str, origin: Origin, params: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        **(params or {}),
        "relevancy": verdict,
        "origin_entity_type": origin.entity_type or None,
        "origin_entity_id": origin.entity_id or None,
        "source_doc": _text(origin.book, 200) or None,
        "source_url": _text(origin.source_url, 500) or None,
        "query": _text(origin.query, 200) or None,
    }


def enqueue_find(
    *,
    provider: str,
    url: str,
    verdict: str,
    origin: Origin,
    summary: str,
    params: Optional[Dict[str, Any]] = None,
    legacy_key: Optional[tuple] = None,
) -> str:
    """Queue one found URL for human review. Returns ``ENQUEUED``, ``ALREADY_QUEUED`` (an earlier
    run queued it: pending, approved, rejected or submitted) or ``REFUSED`` (no positive Grok
    verdict, an unsafe URL, or a provider that is not a find provider). Never raises."""
    clean = _refusal(provider, verdict, url)
    if clean is None:
        return REFUSED
    try:
        if legacy_key and fp.event_recorded(provider, legacy_key):
            return ALREADY_QUEUED
        new = fp.record_fetch_event(
            provider=provider,
            endpoint=clean,
            kind=fp.TEXTUAL,
            outcome=fp.HIT,
            request_params=_event_params(verdict, origin, params),
            result_summary=_text(summary, 300),
            once_key=(origin.key, clean),
        )
    except Exception as e:  # noqa: BLE001 - queueing must never crash a step
        logger.warning(
            "OBSERV find_queue.error provider=%s err=%s status=warn",
            provider,
            type(e).__name__,
        )
        return REFUSED
    if not new:
        return ALREADY_QUEUED
    logger.info(
        "OBSERV find_queue.enqueued provider=%s origin=%s url=%s",
        provider,
        origin.key[:60],
        clean[:120],
    )
    return ENQUEUED
