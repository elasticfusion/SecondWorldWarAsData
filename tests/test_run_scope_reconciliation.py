"""Source-vs-actual run-scope reconciliation ("don't exceed" rule).

Phase 1 records an INDEPENDENT authoritative per-book section count; the run-scope guard reconciles
each phase's ACTUAL work-set against it. RULE: the consuming phase must never process MORE than the
recorded count (fan-out = always a defect → fail loud). Processing FEWER is legitimate (a full run
== recorded; an incremental resume processes only the pending subset). Magic-50 is the FALLBACK only
when no count was recorded.
"""

from pathlib import Path

import pytest

from src.utils import run_scope
from src.utils.run_scope_guard import (
    EXIT_SCOPE_MISMATCH,
    EXIT_UNDECLARED_LARGE_RUN,
    ScopeMismatchError,
    enforce_scope,
)


# --- run_scope record/read ---------------------------------------------------------------
def test_record_then_read_roundtrip(tmp_path: Path):
    assert run_scope.record_scope(tmp_path, "XChannel", 80) is True
    assert run_scope.read_scope(tmp_path, "XChannel") == 80


def test_read_missing_returns_none(tmp_path: Path):
    assert run_scope.read_scope(tmp_path, "NeverRecorded") is None


def test_record_rejects_unsafe_book(tmp_path: Path):
    assert run_scope.record_scope(tmp_path, "../evil", 5) is False
    assert run_scope.record_scope(tmp_path, "a/b", 5) is False


def test_record_rejects_bad_count(tmp_path: Path):
    assert run_scope.record_scope(tmp_path, "Book", -1) is False


# --- enforce_scope with recorded_expected ("don't exceed") -------------------------------
def test_recorded_match_passes_even_above_ceiling():
    # 80 > magic-50, but it EQUALS the recorded source count -> authorized, no declaration needed.
    assert (
        enforce_scope(actual=80, expected=None, recorded_expected=80, label="P2") == 80
    )


def test_recorded_fanout_fails_loud():
    # Processing MORE than Phase 1 produced is never legitimate (dropped --book / bad glob / re-drive).
    with pytest.raises(ScopeMismatchError) as ei:
        enforce_scope(actual=85, expected=None, recorded_expected=80, label="P2")
    assert ei.value.exit_code == EXIT_SCOPE_MISMATCH
    assert "FAN-OUT" in str(ei.value)


def test_recorded_fewer_passes_legit_resume():
    # Processing FEWER than recorded is a legit full/resume subset, NOT an error.
    assert (
        enforce_scope(actual=73, expected=None, recorded_expected=80, label="P2") == 73
    )
    assert enforce_scope(actual=0, expected=None, recorded_expected=80, label="P2") == 0


def test_recorded_takes_precedence_over_declared_expected():
    # recorded is authoritative; a hand-declared `expected` is ignored when recorded is present,
    # as long as actual <= recorded.
    assert enforce_scope(actual=80, expected=79, recorded_expected=80, label="P2") == 80
    assert (
        enforce_scope(actual=50, expected=999, recorded_expected=80, label="P2") == 50
    )
    # but exceeding recorded still fails regardless of the declared value.
    with pytest.raises(ScopeMismatchError):
        enforce_scope(actual=81, expected=81, recorded_expected=80, label="P2")


# --- fallback (no recorded count) = legacy magic-50 behaviour -----------------------------
def test_fallback_small_run_passes_without_record():
    assert (
        enforce_scope(actual=10, expected=None, recorded_expected=None, label="P2")
        == 10
    )


def test_fallback_large_undeclared_raises_without_record():
    with pytest.raises(ScopeMismatchError) as ei:
        enforce_scope(actual=80, expected=None, recorded_expected=None, label="P2")
    assert ei.value.exit_code == EXIT_UNDECLARED_LARGE_RUN


def test_fallback_large_declared_passes():
    assert (
        enforce_scope(actual=80, expected=80, recorded_expected=None, label="P2") == 80
    )


def test_negative_counts_fail_loud():
    with pytest.raises(ScopeMismatchError):
        enforce_scope(actual=-1, expected=None, recorded_expected=None, label="P2")
    with pytest.raises(ScopeMismatchError):
        enforce_scope(actual=5, expected=None, recorded_expected=-3, label="P2")


def test_end_to_end_phase1_record_phase2_reconcile(tmp_path: Path):
    """Phase 1 records 80; Phase 2 reading it + actual 80 reconciles (the chaining-bug fix)."""
    run_scope.record_scope(tmp_path, "XChannel", 80)
    recorded = run_scope.read_scope(tmp_path, "XChannel")
    assert (
        enforce_scope(actual=80, expected=None, recorded_expected=recorded, label="P2")
        == 80
    )
