"""One way to report that an external or AWS service did not answer (task 1.2f, owner
2026-11-18: "deferred yes, dropped never"; every service failure is resumable).

* ``failed(service, op, reason)`` - an outage, not an answer: a counted OBSERV
  ``<service>.unavailable`` warning that says what to check, and a note in the current ``watch()``
  scope so the work item it happened in is NOT recorded as a genuine miss ("not found online",
  "not on archive.org") - it is retried next run instead. With ``stop_after`` (an AWS service a
  run cannot do without) that many failures in a row stop the run like an unreachable Grok
  (``grok_models.ServiceUnreachable``: Phase 3 exits EXIT_CONNECTIVITY, the book is deferred and
  resumes on its own). Outside a phase task (a Lambda, a script) nothing is stopped.
* ``ok(service)`` - the service answered: its failure streak starts again.
* ``watch()`` - collect the outages of one work item (one citation, one image).
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import threading
from typing import Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

_streaks: Dict[str, int] = {}
_lock = threading.Lock()
_scope: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar(
    "service_outage_scope", default=None
)

# what to check, per service (the counted warning must be actionable)
_CHECK = {
    "archive_org": "archive.org status (https://archive.org) and this task's network path",
    "gutendex": "https://gutendex.com and this task's network path",
    "openserp": "the OpenSERP ECS service and its /health",
    "nara": "the NARA API key secret (<env>-wwii-pipeline/nara-api-key) and catalog.archives.gov",
    "dynamodb": "DynamoDB service health and the task role's access to the api-cache table",
    "image_host": "the image host named in the log line (an outage, not a dead link)",
    "s3": "S3 service health and the task role's access to the data bucket",
}


@contextlib.contextmanager
def watch() -> Iterator[List[str]]:
    """The services that failed while this work item ran (empty = every source answered)."""
    seen: List[str] = []
    token = _scope.set(seen)
    try:
        yield seen
    finally:
        _scope.reset(token)


def _in_phase_task() -> bool:
    return os.environ.get("PIPELINE_PHASE", "").startswith("phase")


def failed(service: str, op: str, reason: str, stop_after: Optional[int] = None) -> int:
    """Record one outage of ``service`` (see the module docstring). Returns the streak."""
    from src.utils.observe import observe_warn_counted

    with _lock:
        streak = _streaks.get(service, 0) + 1
        _streaks[service] = streak
    seen = _scope.get()
    if seen is not None and service not in seen:
        seen.append(service)
    observe_warn_counted(
        service,
        "unavailable",
        key=(op,),
        note=(
            f"{service} did not answer ({op}); the work it was for is retried next run, never "
            f"recorded as a miss. Check {_CHECK.get(service, service)} if this repeats"
        ),
        op=op,
        reason=str(reason)[:80],
        streak=streak,
    )
    if stop_after and streak >= stop_after and _in_phase_task():
        from src.utils import grok_models

        grok_models.stop_run(
            grok_models.ServiceUnreachable,
            service,
            f"{streak} {op} failures in a row ({str(reason)[:80]})",
        )
    return streak


# DynamoDB failures in a row before a phase run stops: the URL registry and search cache cannot
# coordinate without it (a few throttles are retried; a lasting outage defers the book)
DYNAMO_STOP_AFTER = 10


def dynamodb_failed(op: str, e: BaseException) -> int:
    """One DynamoDB call failed (not a conditional-check miss): see ``failed``."""
    return failed("dynamodb", op, type(e).__name__, stop_after=DYNAMO_STOP_AFTER)


def ok(service: str) -> None:
    """The service answered: its failure streak starts again."""
    with _lock:
        _streaks.pop(service, None)


def reset() -> None:
    """Forget every streak (tests; a new run in the same process)."""
    with _lock:
        _streaks.clear()
