#!/usr/bin/env python3
"""Progress-watchdog wrapper for the Chandra OCR subprocess.

Why this exists
---------------
The Chandra Batch job's only failure bound used to be the AWS
``AttemptDurationSeconds`` wall-clock timeout (1 h). That is the wrong signal: a
50-page chunk of dense 300-DPI *scanned* pages legitimately takes ~60 min and
was being **killed while healthy and still making progress** (observed on the
St. Vith / Boyer book, chunk p101-150 — died at page ~44/50, no error, just
slow). Meanwhile a genuinely *hung* job (CUDA deadlock, stuck read) produces no
output and would run until the wall-clock limit regardless.

So the bound should measure **lack of progress, not elapsed time**. Chandra
prints a per-page line to stdout as it works::

    Processing pages 33-33...

This wrapper runs Chandra as a subprocess, watches that stream, and:

* resets a timer every time a new page-progress line appears;
* kills the subprocess (and fails the job) only if **no progress** is seen for
  ``OCR_NO_PROGRESS_SECS`` (default 900 s / 15 min) — catching real hangs fast
  while never touching healthy-but-slow work;
* leaves a *generous* absolute ceiling to the AWS ``AttemptDurationSeconds``
  backstop (raised to hours) as a pure cost circuit-breaker, not the working
  limit.

It streams the child's stdout through unchanged, so CloudWatch logs are
identical. Exit code is the child's on normal completion, or non-zero with a
clear ``WATCHDOG:`` reason on a no-progress kill.

Usage (from chandra_entrypoint.sh)::

    python3 scripts/ocr_watchdog.py -- chandra --method hf --page-range 1-50 in.pdf out/
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 - runs the trusted, code-constructed chandra command
import sys
import threading
import time
from typing import List, Optional

# A line Chandra emits when it starts a page. Kept broad so a minor wording
# change still counts as progress (fail-open toward "still working").
_PROGRESS_RE = re.compile(r"processing\s+pages?\b", re.IGNORECASE)

# Any non-empty output is a weaker liveness signal than an explicit page line,
# but a totally silent process for the whole window is the hang we want to kill.
_DEFAULT_NO_PROGRESS_SECS = 900


def _no_progress_limit() -> int:
    raw = os.environ.get("OCR_NO_PROGRESS_SECS", str(_DEFAULT_NO_PROGRESS_SECS))
    try:
        val = int(raw)
        return val if val > 0 else _DEFAULT_NO_PROGRESS_SECS
    except ValueError:
        return _DEFAULT_NO_PROGRESS_SECS


class _Watchdog:
    """Tracks last-progress time and whether a hard stall has occurred."""

    def __init__(self, limit_secs: int) -> None:
        self._limit = limit_secs
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self.stalled = False

    def mark_progress(self) -> None:
        """Record that the child just made progress (a page completed)."""
        with self._lock:
            self._last = time.monotonic()

    def seconds_since_progress(self) -> float:
        """Seconds elapsed since the last recorded progress."""
        with self._lock:
            return time.monotonic() - self._last

    @property
    def limit(self) -> int:
        """The no-progress limit in seconds."""
        return self._limit


def _split_command(argv: List[str]) -> List[str]:
    """Return the child command from argv, supporting an optional ``--`` sep."""
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return argv[1:]


def run(child_cmd: List[str], limit_secs: Optional[int] = None) -> int:
    """Run ``child_cmd`` under the progress watchdog. Returns an exit code."""
    if not child_cmd:
        print("WATCHDOG: no child command given", file=sys.stderr)
        return 2

    limit = limit_secs if limit_secs is not None else _no_progress_limit()
    dog = _Watchdog(limit)

    print(
        f"WATCHDOG: monitoring '{' '.join(child_cmd)}' "
        f"(fail if no page progress for {limit}s)",
        flush=True,
    )

    # nosec B603 - no shell; child_cmd is built in code (entrypoint's chandra
    # invocation), never from untrusted/user-supplied input.
    # pylint: disable=consider-using-with
    # The process is intentionally long-lived and managed across a monitor
    # thread (terminate/kill on stall), so a `with` block does not fit.
    proc = subprocess.Popen(  # nosec B603
        child_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
    )

    def _monitor() -> None:
        # Poll frequently relative to the limit so a small limit is honored and
        # steady progress is never misread as a stall. Cap at 30s for long
        # production limits; floor at 0.2s for fast tests.
        poll = max(0.2, min(30.0, dog.limit / 4.0))
        while proc.poll() is None:
            time.sleep(poll)
            if dog.seconds_since_progress() > dog.limit:
                dog.stalled = True
                print(
                    f"\nWATCHDOG: no page progress for "
                    f"{int(dog.seconds_since_progress())}s "
                    f"(limit {dog.limit}s) — terminating stalled OCR job",
                    file=sys.stderr,
                    flush=True,
                )
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return

    mon = threading.Thread(target=_monitor, daemon=True)
    mon.start()

    # Stream child output, resetting the progress timer on each page line.
    if proc.stdout is not None:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if _PROGRESS_RE.search(line):
                dog.mark_progress()

    proc.wait()
    mon.join(timeout=5)

    if dog.stalled:
        print("WATCHDOG: job failed — stalled (no progress)", file=sys.stderr)
        return 75  # EX_TEMPFAIL — signals a retryable stall to Batch retry

    return proc.returncode


def main() -> int:
    """CLI entry: run the child command (after an optional ``--``) under the watchdog."""
    child = _split_command(sys.argv)
    return run(child)


if __name__ == "__main__":
    sys.exit(main())
