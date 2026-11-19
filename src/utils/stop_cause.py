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

_CAUSE_BY_EXIT = {EXIT_FUNDING: "funding", EXIT_CONNECTIVITY: "connectivity"}


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
