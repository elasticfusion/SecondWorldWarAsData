"""Shared extraction-starvation telemetry helpers (PR-B, 2026-10-28 audit).

The motivating incident: a `paragraphs` vs `paragraph_numbers` key mismatch emptied every
sub-event's `Sub-event_fulltext`, so each entity extractor skipped all sub-events as "empty" at
DEBUG and produced 0 — while every OBSERV step reported `status=ok`. The empty-fulltext skip was
the *direct starvation signature* but was logged below the INFO prod threshold, so a mass
empty-fulltext event was invisible.

These helpers raise that skip to WARNING and count it per event, so an all-sub-events-empty event
emits ONE loud alarm. They complement `src.utils.yield_observer.observe_yield` (the produced==0
baseline): this module answers "did any fulltext reach me at all?", observe_yield answers "given
fulltext, did I produce anything?".

Everything here is FAIL-SAFE: a telemetry error must never crash or halt an extraction phase.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def note_empty_fulltext_skip(
    entity: str, sub_event_id: str, counter: Optional[dict]
) -> None:
    """Record that a sub-event was skipped because its `Sub-event_fulltext` resolved empty.

    Raised from DEBUG to WARNING (vs the old silent debug) because a *mass* empty-fulltext event is
    the starvation fingerprint. ``counter`` (a per-event dict, e.g. ``{"empty": 0}``) is incremented
    so the caller can later compare empty-skips against the sub-event total. Fail-safe: never raises.
    """
    try:
        logger.warning(
            "OBSERV %s.empty_fulltext_skip sub_event=%s — resolved fulltext EMPTY, "
            "sub-event skipped (starvation signature; cf. paragraph_numbers 2026-10-28)",
            entity,
            sub_event_id or "?",
        )
        if isinstance(counter, dict):
            counter["empty"] = int(counter.get("empty", 0)) + 1
    except Exception as e:  # noqa: BLE001 - observability must never break extraction
        logger.debug("note_empty_fulltext_skip skipped for %s: %s", entity, e)


def warn_all_subevents_empty(
    entity: str, event_id: str, sub_events: int, empty: int
) -> None:
    """Emit ONE loud whole-event alarm when EVERY sub-event's fulltext was empty.

    ``empty == sub_events`` (with ``sub_events > 0``) is the unambiguous "the whole event was
    starved of resolved paragraph text" signal that was missing during the incident. Fail-safe.
    """
    try:
        if sub_events > 0 and empty >= sub_events:
            logger.warning(
                "OBSERV %s.all_subevents_empty event=%s sub_events=%d — ALL sub-events had "
                "EMPTY fulltext (upstream data-loss/starvation; check paragraph resolution)",
                entity,
                event_id or "?",
                sub_events,
            )
    except Exception as e:  # noqa: BLE001 - observability must never break extraction
        logger.debug("warn_all_subevents_empty skipped for %s: %s", entity, e)
