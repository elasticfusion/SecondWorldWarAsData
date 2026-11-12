"""Shared per-sub-event discovery: ask about ONE sub-event at a time and account for every request.

Asked for a whole chapter at once (chapter 8: ~37k tokens, 12 sub-events) the model decides how
much is "enough": places 42 / 118 / 89 on three runs, equipment 99 items. One sub-event at a time:
places 227 / 226 / 226, equipment 358 / 331 / 317 (2026-11-12). Input is the same text, split.

Used by the async entity extractors (``batch_parallel._per_sub_event``) and the synchronous
equipment extractor (``run_sync``). Both share:

- ``views``: the event narrowed to each sub-event (every other field kept);
- ``report_failed``: a lost request is counted and alerted - it must not read as a smaller total;
- the stop rule: a model that is gone (``GrokModelUnavailable``) stops the run - requests not yet
  sent are skipped, those in flight finish (their records are saved).
"""

import contextvars
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.grok_client import BatchModeCollecting
from src.utils.grok_models import GrokModelUnavailable
from src.utils.observe import observe_error

logger = logging.getLogger(__name__)

# Requests in flight per event (async: per event loop). Each holds an executor thread while it
# waits for the rate limiter, and the default executor is shared with event extraction.
CONCURRENCY = int(os.environ.get("SUB_EVENT_CONCURRENCY", "8"))


def views(event_data: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """``[(sub_event, event_data narrowed to that one sub-event), ...]`` in source order."""
    event = event_data.get("Event", {}) or {}
    return [
        (se, {**event_data, "Event": {**event, "Sub-events": [se]}})
        for se in event.get("Sub-events", []) or []
    ]


def describe_error(se: Dict[str, Any], exc: BaseException) -> str:
    return f"{se.get('Sub-eventID', '')}: {type(exc).__name__}: {exc}"


def report_failed(
    entity_type: str,
    failed: int,
    of: int,
    event_data: Dict[str, Any],
    errors: List[str],
) -> None:
    """Alert on sub-events whose request failed (no-op when none did)."""
    if not failed:
        return
    observe_error(
        entity_type or "entities",
        "sub_events_failed",
        failed=failed,
        of=of,
        event_id=(event_data.get("Event") or {}).get("EventID", ""),
        errors=errors[:5],
        note="these sub-events' requests failed: their entities are missing, not zero",
    )


def run_sync(
    extract_one: Callable[[Dict[str, Any], Dict[str, Any]], Any],
    event_data: Dict[str, Any],
    entity_type: str,
    workers: Optional[int] = None,
) -> List[Any]:
    """Call ``extract_one(sub_event, narrowed_event)`` for every sub-event on a thread pool and
    return the successful results in source order.

    - An ordinary exception is a failed sub-event: logged, counted, alerted, the rest kept.
    - ``BatchModeCollecting`` (batch collect pass): every sub-event's request is queued first,
      then it is re-raised once, as the single-call path did.
    - ``GrokModelUnavailable`` stops the run: requests not yet sent are skipped, then it is raised.
    """
    pairs = views(event_data)
    stopped = threading.Event()
    skipped = object()

    def call(pair: Tuple[Dict[str, Any], Dict[str, Any]]) -> Any:
        if stopped.is_set():
            return skipped
        try:
            return extract_one(*pair)
        except GrokModelUnavailable:
            stopped.set()
            raise

    def task(job: Tuple[contextvars.Context, Any]) -> Tuple[str, Any]:
        ctx, pair = job
        try:
            return "ok", ctx.run(call, pair)
        except BaseException as e:  # noqa: BLE001 - sorted below; nothing is swallowed
            return "error", e

    # request identity (the chapter's book) is a context variable: copy the CALLER's context, one
    # copy per sub-event (a Context cannot be entered by two threads at once)
    jobs = [(contextvars.copy_context(), pair) for pair in pairs]
    n = max(1, min(workers or CONCURRENCY, len(pairs) or 1))
    with ThreadPoolExecutor(
        max_workers=n, thread_name_prefix=f"{entity_type}-se"
    ) as ex:
        outcomes = list(ex.map(task, jobs))
    return _sort_outcomes(pairs, outcomes, skipped, event_data, entity_type)


def _sort_outcomes(
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    outcomes: List[Tuple[str, Any]],
    skipped: object,
    event_data: Dict[str, Any],
    entity_type: str,
) -> List[Any]:
    results, raised = _partition(pairs, outcomes, skipped)
    _raise_stop(raised)
    collecting = [v for _, v in raised if isinstance(v, BatchModeCollecting)]
    failed = [(se, v) for se, v in raised if not isinstance(v, BatchModeCollecting)]
    _report(failed, len(pairs), event_data, entity_type)
    if collecting:
        raise collecting[0]
    return results


def _partition(
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]],
    outcomes: List[Tuple[str, Any]],
    skipped: object,
) -> Tuple[List[Any], List[Tuple[Dict[str, Any], BaseException]]]:
    """(results of the requests sent, [(sub_event, exception), ...])."""
    results = [v for kind, v in outcomes if kind == "ok" and v is not skipped]
    raised = [(se, v) for (se, _), (kind, v) in zip(pairs, outcomes) if kind == "error"]
    return results, raised


def _raise_stop(raised: List[Tuple[Dict[str, Any], BaseException]]) -> None:
    """A model stop first; then anything that is not an error to count (KeyboardInterrupt...)."""
    fatal = [v for _, v in raised if not isinstance(v, Exception)]
    stops = [v for v in fatal if isinstance(v, GrokModelUnavailable)]
    if fatal:
        raise (stops or fatal)[0]


def _report(
    failed: List[Tuple[Dict[str, Any], BaseException]],
    of: int,
    event_data: Dict[str, Any],
    entity_type: str,
) -> None:
    for se, v in failed:
        logger.error(
            "%s sub-event %s failed", entity_type, se.get("Sub-eventID"), exc_info=v
        )
    errors = [describe_error(se, v) for se, v in failed]
    report_failed(entity_type, len(errors), of, event_data, errors)
