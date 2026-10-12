"""Shared, UNOVERRIDABLE run-scope safeguard (Phase 2 + Phase 3)."""

# pylint: disable=missing-function-docstring,import-error

import logging

import pytest

from src.utils import run_scope_guard as g
from src.utils.run_scope_guard import ScopeMismatchError

log = logging.getLogger("test")


def test_small_undeclared_run_passes():
    # Below the ceiling, no declaration needed.
    assert g.enforce_scope(actual=3, expected=None, label="x", logger=log) == 3


def test_declared_match_passes_even_above_ceiling():
    # Explicit declaration of a large run is allowed (intent proven).
    big = g.LARGE_RUN_CEILING + 100
    assert g.enforce_scope(actual=big, expected=big, label="x", logger=log) == big


def test_declared_mismatch_aborts():
    with pytest.raises(ScopeMismatchError) as exc:
        g.enforce_scope(actual=842, expected=3, label="x", logger=log)
    assert exc.value.exit_code == g.EXIT_SCOPE_MISMATCH


def test_undeclared_large_run_aborts():
    # The core protection: a big run with NO declaration trips the ceiling.
    with pytest.raises(ScopeMismatchError) as exc:
        g.enforce_scope(
            actual=g.LARGE_RUN_CEILING + 1, expected=None, label="x", logger=log
        )
    assert exc.value.exit_code == g.EXIT_UNDECLARED_LARGE_RUN


def test_no_flag_or_kwarg_disables_the_guard():
    """There is no 'force'/'skip' parameter; the signature cannot express a
    bypass. Declaring the exact count is the only way through a large run."""
    import inspect

    params = set(inspect.signature(g.enforce_scope).parameters)
    assert not params & {"force", "skip", "override", "yes", "disable"}


def test_ceiling_boundary_exact_passes_one_over_aborts():
    # actual == ceiling passes (strict >), actual == ceiling+1 aborts.
    assert (
        g.enforce_scope(
            actual=g.LARGE_RUN_CEILING, expected=None, label="x", logger=log
        )
        == g.LARGE_RUN_CEILING
    )
    with pytest.raises(ScopeMismatchError):
        g.enforce_scope(
            actual=g.LARGE_RUN_CEILING + 1, expected=None, label="x", logger=log
        )


def test_negative_counts_abort():
    with pytest.raises(ScopeMismatchError) as e1:
        g.enforce_scope(actual=-1, expected=-1, label="x", logger=log)
    assert e1.value.exit_code == g.EXIT_SCOPE_MISMATCH
    with pytest.raises(ScopeMismatchError):
        g.enforce_scope(actual=5, expected=-1, label="x", logger=log)
