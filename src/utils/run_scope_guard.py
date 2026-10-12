"""Shared run-scope safeguard for Phase 2, Phase 3, and the AWS dispatcher.

A scoped intent must never silently become a whole-corpus / whole-backlog run.
Every entry point knows how many items it is *about to* process; this primitive
compares that actual count against the operator-declared expectation and a hard
ceiling, and FAILS LOUD on disagreement.

Design contract (owner-directed):
  * The safeguard is UNCONDITIONAL. There is deliberately NO ``--force`` / env /
    config switch that disables it. The ONLY way to process a large set is to
    DECLARE it explicitly (``expected``) — a statement of intent the guard checks
    against, not a bypass of the guard.
  * ``expected`` set and != actual         -> raise (declared-scope mismatch).
  * actual > ceiling and not explicitly declared -> raise (undeclared large run).

The "actual" count MUST be the authoritative work set for the mode:
  * local Phase 2  -> number of in-scope ``*-parsed.json`` files
  * local Phase 3  -> corpus entity-record count (tripwire; see phase3 note)
  * AWS dispatcher -> number of DISPATCHABLE ``doc#`` lifecycle records in
    DynamoDB (the real queued work set — NOT an S3 file count).

The primitive raises :class:`ScopeMismatchError` (carrying an ``exit_code``);
CLI callers convert it to ``SystemExit``, the dispatcher Lambda converts it to a
fail-loud structured abort + alert. The guard itself never calls ``sys.exit`` so
it is safe to embed in a Lambda.
"""

from __future__ import annotations

import logging
from typing import Optional

# Absolute ceiling: a run/dispatch touching more than this many items must be
# explicitly declared (proving intent). Not overridable by any flag/env/config.
#
# NOTE: this is a SCOPE tripwire (guards an *unintended* large fan-out), NOT a
# concurrency cap. Running many batches in parallel is a supported goal; it is
# governed separately (dispatcher clamp_pool / SFN Map MaxConcurrency). A large
# INTENTIONAL drain — however parallel — is allowed by DECLARING its size via
# ``expected``. The guard never limits how many run at once, only forbids a large
# work set that nobody declared.
LARGE_RUN_CEILING = 50

# Distinct codes so CLI exit status / CI / alerts can tell the modes apart.
EXIT_SCOPE_MISMATCH = 3
EXIT_UNDECLARED_LARGE_RUN = 4


class ScopeMismatchError(RuntimeError):
    """The actual work-set size disagrees with the declared/allowed scope.

    Carries ``exit_code`` so a CLI caller can ``raise SystemExit(e.exit_code)``
    and a service caller can log/alert and abort without exiting the process.
    """

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def enforce_scope(
    *,
    actual: int,
    expected: Optional[int],
    label: str,
    logger: Optional[logging.Logger] = None,
    ceiling: int = LARGE_RUN_CEILING,
) -> int:
    """Enforce the run-scope contract. Returns ``actual`` on success; raises
    :class:`ScopeMismatchError` (fail-loud) on any violation.

    Args:
        actual: number of items about to be processed (known now; the
            authoritative work set for the mode — see module docstring).
        expected: operator-declared expected count, or ``None`` if undeclared.
        label: phase/entity label for the message.
        ceiling: absolute size above which an explicit declaration is required.
    """
    log = logger or logging.getLogger(__name__)

    # Defensive: counts come from len()/scan (>=0) and int inputs (may be
    # negative). A negative on either side is a miswired caller — fail loud.
    if actual < 0 or (expected is not None and expected < 0):
        msg = f"INVALID SCOPE ({label}): negative count (actual={actual}, expected={expected})."
        log.error(msg)
        raise ScopeMismatchError(msg, EXIT_SCOPE_MISMATCH)

    # 1. Declared-scope mismatch — the explicit known-vs-actual check.
    if expected is not None and actual != expected:
        msg = (
            f"SCOPE MISMATCH ({label}): you declared {expected} but {actual} are "
            f"about to be processed. Aborting for reconsideration (fail-loud; this "
            f"safeguard cannot be overridden). Re-check and re-run with the correct scope."
        )
        log.error(msg)
        raise ScopeMismatchError(msg, EXIT_SCOPE_MISMATCH)

    # 2. Unconditional large-run backstop — fires even when expected is None, so
    #    an unintended whole-corpus/backlog run always trips it. Satisfied ONLY by
    #    an explicit matching declaration (expected == actual), never by a flag.
    #    The exact count is deliberately NOT echoed (anti rubber-stamp).
    if actual > ceiling and expected != actual:
        msg = (
            f"UNDECLARED LARGE RUN ({label}): the number of items to process "
            f"exceeds the ceiling of {ceiling} and no matching expected-count was "
            f"declared. Aborting (fail-loud; not overridable). Determine the "
            f"intended scope and, if a large run is truly intended, declare that "
            f"exact count explicitly."
        )
        log.error(msg)
        raise ScopeMismatchError(msg, EXIT_UNDECLARED_LARGE_RUN)

    return actual
