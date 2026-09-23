"""Tests for the OCR progress-watchdog wrapper (scripts/ocr_watchdog.py).

The watchdog runs an OCR subprocess and fails the job on *lack of progress*
(no page-progress line within a window), not on wall-clock time. These tests
drive it with tiny synthetic child commands so no GPU/Chandra is needed.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ocr_watchdog",
    str(Path(__file__).resolve().parents[1] / "scripts" / "ocr_watchdog.py"),
)
ocr_watchdog = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ocr_watchdog)  # type: ignore[union-attr]


def _py(code: str) -> list:
    """Build a child command that runs inline Python."""
    return [sys.executable, "-c", textwrap.dedent(code)]


def test_normal_completion_returns_child_exit_code():
    """A child that emits progress and exits 0 passes through as 0."""
    cmd = _py("""
        import time
        for p in range(3):
            print(f"Processing pages {p}-{p}...", flush=True)
            time.sleep(0.05)
        """)
    assert ocr_watchdog.run(cmd, limit_secs=5) == 0


def test_child_nonzero_exit_propagates():
    cmd = _py("import sys; print('Processing pages 1-1...', flush=True); sys.exit(3)")
    assert ocr_watchdog.run(cmd, limit_secs=5) == 3


def test_stall_is_killed_and_fails():
    """A child that goes silent past the limit is terminated with non-zero."""
    cmd = _py("""
        import time
        print("Processing pages 1-1...", flush=True)
        time.sleep(30)          # stall well past the 1s limit
        print("Processing pages 2-2...", flush=True)
        """)
    rc = ocr_watchdog.run(cmd, limit_secs=1)
    assert rc != 0


def test_progress_resets_the_timer():
    """Steady progress just under the limit must NOT be killed."""
    cmd = _py("""
        import time
        for p in range(6):
            print(f"Processing pages {p}-{p}...", flush=True)
            time.sleep(0.4)     # each < 1s limit; total 2.4s > limit if not reset
        """)
    assert ocr_watchdog.run(cmd, limit_secs=1) == 0


def test_no_progress_limit_env(monkeypatch):
    monkeypatch.setenv("OCR_NO_PROGRESS_SECS", "1234")
    assert ocr_watchdog._no_progress_limit() == 1234
    monkeypatch.setenv("OCR_NO_PROGRESS_SECS", "bogus")
    assert ocr_watchdog._no_progress_limit() == 900


def test_empty_command_fails():
    assert ocr_watchdog.run([]) == 2


def test_split_command_supports_dash_dash():
    assert ocr_watchdog._split_command(["ocr_watchdog.py", "--", "chandra", "x"]) == [
        "chandra",
        "x",
    ]
    assert ocr_watchdog._split_command(["ocr_watchdog.py", "chandra", "x"]) == [
        "chandra",
        "x",
    ]
