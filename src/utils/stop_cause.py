"""Exit codes a phase script uses to say WHY it stopped, when the cause is not the book's fault
(task 1.2b, owner 2026-11-18). The ECS wrapper maps them to a queue deferral
(src/utils/pending_queue.defer) that gives the attempt back and resumes automatically, instead of
counting a failure. Any other non-zero exit is an ordinary failure (counted, capped).

Raised by: Grok funds running out mid-run, Grok or the network unreachable after the shared
client's retries (D8, task 1.2e: ``grok_models.GrokFundsExhausted`` / ``GrokUnreachable``, mapped
here by ``run_main``). 3/4 are run_scope_guard's and 75 is Phase 2's batch hand-off.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

EXIT_FUNDING = 80
EXIT_CONNECTIVITY = 81
# A Phase 3 step raised and stopped partway (owner 2026-11-18): the book is NOT finished - it stays
# queued and is retried (an ordinary failure: counted, capped). Data corruption is the exception:
# a retry cannot fix it, so the book is held for the operator and the alert says so loudly.
EXIT_STEP_FAILED = 82
EXIT_DATA_CORRUPT = 83

_CAUSE_BY_EXIT = {
    EXIT_FUNDING: "funding",
    EXIT_CONNECTIVITY: "connectivity",
    EXIT_DATA_CORRUPT: "failure",  # held for the operator (pending_queue MANUAL_RESUME)
}


class RecordCorrupt(ValueError):
    """A record the book has already STORED (``output/<entity>/*.json``) cannot be read or fails
    its schema. Raised only by the readers of stored records (``entity_records.read_record``),
    never for a service answer: an HTML error page, a Grok reply that does not parse or a write
    guard rejecting an LLM answer are retryable faults of a service, and classing them as
    corruption would let untrusted content hold a book. A ``ValueError`` so existing
    ``except ValueError`` handlers keep working."""

    def __init__(self, path: Any, reason: str):
        super().__init__(f"stored record {path} is corrupt: {reason}")
        self.path = str(path)


def is_data_corruption(error: BaseException) -> bool:
    """True if ``error`` is, or was raised from, a ``RecordCorrupt`` (the book's own stored data
    is bad, so a retry cannot fix it). Walks the explicit ``__cause__`` chain (``raise X from
    e``), so a step that wraps the error keeps the classification. The implicit ``__context__``
    is not followed: a step that caught a RecordCorrupt, set the record aside and then hit an
    unrelated error inside that ``except`` must not hold the book. Any other error is not
    corruption."""
    seen: set = set()
    e: Optional[BaseException] = error
    while e is not None and id(e) not in seen:
        if isinstance(e, RecordCorrupt):
            return True
        seen.add(id(e))
        e = e.__cause__
    return False


def run_main(main: Callable[[], Any]) -> Any:
    """Run a phase script's ``main`` and turn a Grok stop into its exit code. Other exits and
    exceptions are unchanged (a model that is gone still stops the run with an error).
    """
    from src.utils.grok_models import GrokFundsExhausted, GrokUnreachable

    try:
        return main()
    except GrokFundsExhausted as e:
        logger.error("OBSERV phase.stopped status=error cause=funding detail=%s", e)
        return EXIT_FUNDING
    except GrokUnreachable as e:
        logger.error(
            "OBSERV phase.stopped status=error cause=connectivity detail=%s", e
        )
        return EXIT_CONNECTIVITY


def cause_for_exit(returncode: int) -> Optional[str]:
    """The deferral cause for a phase exit code, or None for an ordinary failure."""
    return _CAUSE_BY_EXIT.get(returncode)
