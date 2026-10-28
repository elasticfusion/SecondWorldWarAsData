"""Expected-yield observability — the single highest-leverage fix from the 2026-10-28 audit.

The pipeline's telemetry was *activity-oriented*: every step logged what it DID (count=0, status=ok)
but never what it SHOULD have produced. So `produced=0` was indistinguishable from `eligible=0`, and
a data-loss bug (the `paragraph_numbers`→empty-`Sub-event_fulltext` starvation that yielded 0
equipment / 7 places / 6 groups) passed every OBSERV step as healthy.

`observe_yield` encodes the two alarms both audit tracks converged on:
  • fulltext_nonempty == 0 AND sub_events > 0  -> STARVATION / data-loss upstream (the incident)
  • produced == 0        AND fulltext_nonempty > 0 -> EXTRACTION REGRESSION (input present, nothing out)

It emits a single greppable `OBSERV yield.<entity>` line (INFO when healthy, WARNING when an alarm
trips) carrying the baseline (sub_events, fulltext_nonempty) alongside produced — so `produced=0` is
never again ambiguous. Fail-safe: observability must never raise into extraction.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def observe_yield(
    entity: str,
    event_id: str,
    sub_events: int,
    fulltext_nonempty: int,
    produced: int,
) -> str:
    """Emit `OBSERV yield.<entity>` with the expected-yield baseline + an anomaly verdict.

    Args:
        entity: entity type (e.g. "equipment", "places").
        event_id: owning EventID (for correlation back to the event pass).
        sub_events: number of sub-events the event pass produced (upstream baseline).
        fulltext_nonempty: how many of those sub-events carried REAL resolved paragraph text
            (not a summary fallback) — the input actually available to extraction.
        produced: entities this pass actually extracted/persisted.

    Returns the verdict string ("ok" | "starvation" | "regression"). Never raises.
    """
    try:
        verdict = _classify_yield(sub_events, fulltext_nonempty, produced)
        line = "OBSERV yield.%s event=%s sub_events=%d fulltext_nonempty=%d produced=%d verdict=%s"
        args = (entity, event_id, sub_events, fulltext_nonempty, produced, verdict)
        if verdict == "starvation":
            logger.warning(
                line
                + " — sub-events exist but NO resolved fulltext reached extraction "
                "(upstream data-loss; cf. paragraph_numbers 2026-10-28)",
                *args,
            )
        elif verdict == "regression":
            logger.warning(
                line + " — fulltext was present but extraction produced NOTHING "
                "(extraction regression)",
                *args,
            )
        else:
            logger.info(line, *args)
        return verdict
    except Exception as e:  # noqa: BLE001 - observability must never break extraction
        logger.debug("observe_yield skipped for %s: %s", entity, e)
        return "ok"


def _classify_yield(sub_events: int, fulltext_nonempty: int, produced: int) -> str:
    """Pure verdict logic (testable without logging side effects)."""
    if sub_events > 0 and fulltext_nonempty == 0:
        return "starvation"
    if fulltext_nonempty > 0 and produced == 0:
        return "regression"
    return "ok"


def _has_real_fulltext(se: dict) -> bool:
    """True if a sub-event's Sub-event_fulltext holds real paragraph text (not a summary fallback)."""
    if not isinstance(se, dict):
        return False
    ft = se.get("Sub-event_fulltext")
    if not isinstance(ft, dict):
        return False
    return any(k != "summary" and str(v).strip() for k, v in ft.items())


def count_nonempty_fulltext(event_data: Optional[dict]) -> int:
    """Count sub-events whose `Sub-event_fulltext` holds REAL paragraph text (not a summary
    fallback). A dict keyed by `Paragraph_*` is real; a `{"summary": ...}` fallback is NOT. This is
    the `fulltext_nonempty` baseline for observe_yield. Fail-safe -> 0.
    """
    try:
        if not isinstance(event_data, dict):
            return 0
        subs = (event_data.get("Event") or {}).get("Sub-events") or []
        return sum(1 for se in subs if _has_real_fulltext(se))
    except Exception:  # noqa: BLE001
        return 0
