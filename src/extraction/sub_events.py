"""Shared fan-out of Grok requests: ask about ONE sub-event (or one item) at a time and account for
every request.

Asked for a whole chapter at once (chapter 8: ~37k tokens, 12 sub-events) the model decides how
much is "enough": places 42 / 118 / 89 on three runs, equipment 99 items. One sub-event at a time:
places 227 / 226 / 226, equipment 358 / 331 / 317 (2026-11-12). Input is the same text, split.

Used by the async entity extractors (``batch_parallel._per_sub_event``) and the synchronous
equipment extractor (``run_sync`` per sub-event, ``run_all`` per item). They share:

- ``views``: the event narrowed to each sub-event (every other field kept);
- ``report_failed``: a lost request is counted and alerted - it must not read as a smaller total;
- the stop rule: a model that is gone (``GrokModelUnavailable``) stops the run - requests not yet
  sent are skipped, those in flight finish (their records are saved);
- the batch rule: on a batch collect pass EVERY item's request is queued before
  ``BatchModeCollecting`` is raised (once), so one stage of a chapter is one batch round - not one
  round per item.
"""

import contextvars
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from src.grok_client import BatchModeCollecting, GrokIdentityError
from src.utils.grok_models import GrokModelUnavailable
from src.utils.observe import observe_error

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

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


def describe_error(label: str, exc: BaseException) -> str:
    return f"{label}: {type(exc).__name__}: {exc}"


def report_failed(
    entity_type: str,
    failed: int,
    of: int,
    event_data: Dict[str, Any],
    errors: List[str],
    what: str = "sub_events",
) -> None:
    """Alert on sub-events (or items) whose request failed (no-op when none did)."""
    if not failed:
        return
    observe_error(
        entity_type or "entities",
        f"{what}_failed",
        failed=failed,
        of=of,
        event_id=(event_data.get("Event") or {}).get("EventID", ""),
        errors=errors[:5],
        note=f"these {what.replace('_', '-')}' requests failed: their entities are missing, "
        "not zero",
    )


def run_sync(
    extract_one: Callable[[Dict[str, Any], Dict[str, Any]], Any],
    event_data: Dict[str, Any],
    entity_type: str,
    workers: Optional[int] = None,
) -> List[Any]:
    """``run_all`` over the sub-events: ``extract_one(sub_event, narrowed_event)`` for each,
    the successful results in source order."""
    done = run_all(
        lambda pair: extract_one(*pair),
        views(event_data),
        entity_type,
        event_data,
        label=lambda pair: str(pair[0].get("Sub-eventID", "")),
        workers=workers,
    )
    return [result for _pair, result in done]


def run_all(
    fn: Callable[[T], R],
    items: Sequence[T],
    entity_type: str,
    event_data: Dict[str, Any],
    label: Callable[[T], str] = lambda _item: "",
    workers: Optional[int] = None,
    what: str = "sub_events",
    failures: Optional[List[Tuple[str, BaseException]]] = None,
) -> List[Tuple[T, R]]:
    """Call ``fn(item)`` for every item on a thread pool and return ``[(item, result), ...]`` for
    the items that completed, in input order. ``workers=1`` runs them one after another (for
    steps that write shared files).

    - An ordinary exception is a failed item: logged, counted, alerted, the rest kept; it is
      appended to ``failures`` when given (a caller that must retry the chapter checks it).
    - ``BatchModeCollecting`` (batch collect pass): every item runs first (each queues its
      request), then it is re-raised once.
    - ``GrokModelUnavailable`` stops the run: items not yet started are skipped, then it is raised.
    - ``GrokIdentityError`` (an answer that belongs to another request) is raised once every item
      has run: it is not a data failure to count.
    """
    stopped = threading.Event()
    skipped = object()

    def call(item: T) -> Any:
        if stopped.is_set():
            return skipped
        try:
            return fn(item)
        except GrokModelUnavailable:
            stopped.set()
            raise

    def task(job: Tuple[contextvars.Context, T]) -> Tuple[str, Any]:
        ctx, item = job
        try:
            return "ok", ctx.run(call, item)
        except BaseException as e:  # noqa: BLE001 - sorted below; nothing is swallowed
            return "error", e

    # request identity (the chapter's book) is a context variable: copy the CALLER's context, one
    # copy per item (a Context cannot be entered by two threads at once)
    jobs = [(contextvars.copy_context(), item) for item in items]
    n = max(1, min(workers or CONCURRENCY, len(jobs) or 1))
    with ThreadPoolExecutor(
        max_workers=n, thread_name_prefix=f"{entity_type}-fan"
    ) as ex:
        outcomes = list(ex.map(task, jobs))
    labelled = [(item, label(item)) for item in items]
    done, raised = _partition(labelled, outcomes, skipped)
    _settle(raised, len(jobs), event_data, entity_type, what, failures)
    return done


def _settle(
    raised: List[Tuple[str, BaseException]],
    of: int,
    event_data: Dict[str, Any],
    entity_type: str,
    what: str,
    failures: Optional[List[Tuple[str, BaseException]]],
) -> None:
    """Raise what stops the run, count + alert the failed items, then re-raise a collect."""
    _raise_stop(raised)
    collecting = [v for _, v in raised if isinstance(v, BatchModeCollecting)]
    failed = [(lb, v) for lb, v in raised if not isinstance(v, BatchModeCollecting)]
    _report(failed, of, event_data, entity_type, what)
    if failures is not None:
        failures.extend(failed)
    if collecting:
        raise collecting[0]


def _partition(
    labelled: List[Tuple[Any, str]],
    outcomes: List[Tuple[str, Any]],
    skipped: object,
) -> Tuple[List[Tuple[Any, Any]], List[Tuple[str, BaseException]]]:
    """([(item, result) of the items that completed], [(label, exception), ...])."""
    done = [
        (item, v)
        for (item, _lb), (kind, v) in zip(labelled, outcomes)
        if kind == "ok" and v is not skipped
    ]
    raised = [
        (lb, v) for (_item, lb), (kind, v) in zip(labelled, outcomes) if kind == "error"
    ]
    return done, raised


def _raise_stop(raised: List[Tuple[str, BaseException]]) -> None:
    """A model stop first; then anything that is not an error to count (KeyboardInterrupt...);
    then an answer identity mismatch."""
    fatal = [v for _, v in raised if not isinstance(v, Exception)]
    stops = [v for v in fatal if isinstance(v, GrokModelUnavailable)]
    if fatal:
        raise (stops or fatal)[0]
    mismatches = [v for _, v in raised if isinstance(v, GrokIdentityError)]
    if mismatches:
        raise mismatches[0]


def _report(
    failed: List[Tuple[str, BaseException]],
    of: int,
    event_data: Dict[str, Any],
    entity_type: str,
    what: str,
) -> None:
    for lb, v in failed:
        logger.error("%s %s %s failed", entity_type, what, lb, exc_info=v)
    errors = [describe_error(lb, v) for lb, v in failed]
    report_failed(entity_type, len(errors), of, event_data, errors, what)
