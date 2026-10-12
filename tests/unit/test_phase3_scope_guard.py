"""Phase 3 corpus-size scope guard (shares src.utils.run_scope_guard).

Phase 3 counts entity records across the dirs it may enrich and enforces the
same unoverridable rule as Phase 2. These tests exercise that counting +
enforcement contract directly (mirroring phase3_enrich_data.main()'s guard
block) without running real enrichment.
"""

# pylint: disable=missing-function-docstring,import-error

import logging

import pytest

from src.utils.run_scope_guard import (
    enforce_scope,
    EXIT_UNDECLARED_LARGE_RUN,
    LARGE_RUN_CEILING,
    ScopeMismatchError,
)

log = logging.getLogger("test")

_PHASE3_DIRS = ["people_groups", "places", "people", "equipment", "bibliography"]


def _seed(out, counts: dict):
    for d, n in counts.items():
        p = out / d
        p.mkdir(parents=True, exist_ok=True)
        (p / "index.json").write_text("{}", encoding="utf-8")  # must be excluded
        for i in range(n):
            (p / f"{d}_{i}.json").write_text("{}", encoding="utf-8")


def _count(out, people_only=False):
    dirs = list(_PHASE3_DIRS) + ([] if people_only else ["dates", "maps"])
    total = 0
    for d in dirs:
        p = out / d
        if p.is_dir():
            total += sum(1 for f in p.glob("*.json") if f.name != "index.json")
    return total


def test_small_corpus_passes_undeclared(tmp_path):
    _seed(tmp_path, {"people": 3, "places": 2})
    assert (
        enforce_scope(
            actual=_count(tmp_path), expected=None, label="Phase 3", logger=log
        )
        == 5
    )


def test_large_corpus_undeclared_aborts(tmp_path):
    _seed(tmp_path, {"people": LARGE_RUN_CEILING + 10, "places": 5})
    with pytest.raises(ScopeMismatchError) as exc:
        enforce_scope(
            actual=_count(tmp_path), expected=None, label="Phase 3", logger=log
        )
    assert exc.value.exit_code == EXIT_UNDECLARED_LARGE_RUN


def test_large_corpus_declared_passes(tmp_path):
    _seed(tmp_path, {"people": LARGE_RUN_CEILING + 10, "places": 5})
    n = _count(tmp_path)
    assert enforce_scope(actual=n, expected=n, label="Phase 3", logger=log) == n


def test_index_json_excluded_from_count(tmp_path):
    _seed(tmp_path, {"people": 2})
    # 2 records + 1 index.json in the dir; index.json must not be counted.
    assert _count(tmp_path) == 2
