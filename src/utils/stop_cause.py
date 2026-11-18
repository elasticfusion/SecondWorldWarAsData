"""Exit codes a phase script uses to say WHY it stopped, when the cause is not the book's fault
(task 1.2b, owner 2026-11-18). The ECS wrapper maps them to a queue deferral
(src/utils/pending_queue.defer) that gives the attempt back and resumes automatically, instead of
counting a failure. Any other non-zero exit is an ordinary failure (counted, capped).

Raised by: Grok funds running out mid-run (1.1c), Grok or the network unreachable after the shared
client's retries (D8, 1.2e). 3/4 are run_scope_guard's and 75 is Phase 2's batch hand-off.
"""

from __future__ import annotations

from typing import Optional

EXIT_FUNDING = 80
EXIT_CONNECTIVITY = 81

_CAUSE_BY_EXIT = {EXIT_FUNDING: "funding", EXIT_CONNECTIVITY: "connectivity"}


def cause_for_exit(returncode: int) -> Optional[str]:
    """The deferral cause for a phase exit code, or None for an ordinary failure."""
    return _CAUSE_BY_EXIT.get(returncode)
