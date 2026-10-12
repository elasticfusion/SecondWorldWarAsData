"""Phase 2 scope guard: a scoped intent must never silently become a
whole-corpus run. When the number of chapters about to be processed disagrees
with the operator-declared expected scope, the run ABORTS (fail-loud).
"""

# pylint: disable=missing-function-docstring,import-error

import logging

import pytest

import phase2_extract as p2

log = logging.getLogger("test")


def _mk(content_root, book, n):
    d = content_root / book
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(n):
        f = d / f"chapter{i}a-parsed.json"
        f.write_text("{}", encoding="utf-8")
        files.append(f)
    return files


def test_expected_count_match_passes(tmp_path):
    files = _mk(tmp_path, "SiegfriedLine", 3)
    out = p2._apply_scope_guard(files, None, 3, tmp_path, log)
    assert len(out) == 3


def test_expected_count_mismatch_aborts(tmp_path):
    # 842 "corpus" files but operator expected only 3 -> must abort (mismatch).
    files = _mk(tmp_path, "SiegfriedLine", 3) + _mk(tmp_path, "BreakoutAndPursuit", 839)
    with pytest.raises(SystemExit) as exc:
        p2._apply_scope_guard(files, None, 3, tmp_path, log)
    assert exc.value.code == 3  # EXIT_SCOPE_MISMATCH


def test_undeclared_whole_corpus_aborts(tmp_path):
    # No --expected-count, no --book: a large undeclared run must trip the
    # unconditional ceiling (the owner's core protection).
    files = _mk(tmp_path, "SiegfriedLine", 3) + _mk(tmp_path, "BreakoutAndPursuit", 839)
    with pytest.raises(SystemExit) as exc:
        p2._apply_scope_guard(files, None, None, tmp_path, log)
    assert exc.value.code == 4  # EXIT_UNDECLARED_LARGE_RUN


def test_book_scopes_to_single_book(tmp_path):
    files = _mk(tmp_path, "SiegfriedLine", 3) + _mk(tmp_path, "BreakoutAndPursuit", 32)
    out = p2._apply_scope_guard(files, "SiegfriedLine", None, tmp_path, log)
    assert len(out) == 3
    assert all("SiegfriedLine" in str(p) for p in out)


def test_book_plus_expected_count_together(tmp_path):
    files = _mk(tmp_path, "SiegfriedLine", 3) + _mk(tmp_path, "BreakoutAndPursuit", 32)
    out = p2._apply_scope_guard(files, "SiegfriedLine", 3, tmp_path, log)
    assert len(out) == 3


def test_book_scope_then_count_mismatch_aborts(tmp_path):
    files = _mk(tmp_path, "SiegfriedLine", 3) + _mk(tmp_path, "BreakoutAndPursuit", 32)
    with pytest.raises(SystemExit) as exc:
        p2._apply_scope_guard(files, "SiegfriedLine", 5, tmp_path, log)
    assert exc.value.code == 3


def test_unknown_book_aborts_not_falls_through(tmp_path):
    files = _mk(tmp_path, "SiegfriedLine", 3)
    with pytest.raises(SystemExit) as exc:
        p2._apply_scope_guard(files, "NoSuchBook", None, tmp_path, log)
    assert exc.value.code == 2
