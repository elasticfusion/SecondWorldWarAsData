"""P3 observability: the Phase-2 end-of-run per-entity-type persisted-count summary.

_log_phase2_summary emits ONE OBSERV phase2.summary line with per-type counts so a zero-output
bug (the optional-entity-zero / people-bio-zero class) is visible immediately. It must:
  * count file-per-record entity dirs, excluding index/duplicate_report/hidden files,
  * count events under content/<book>/*-event.json (not a flat dir),
  * flag entity types at 0,
  * be fail-safe (never raise) on a missing/odd root.
"""

import logging

import pytest
from pathlib import Path

from phase2_extract import _log_phase2_summary, _count_entity_dir


def _w(p: Path, name: str):
    p.mkdir(parents=True, exist_ok=True)
    (p / name).write_text("{}", encoding="utf-8")


def test_counts_entities_and_events_excluding_index(tmp_path):
    root = tmp_path / "output"
    # people: 2 real + index + duplicate_report + hidden -> should count 2
    _w(root / "people", "alice.json")
    _w(root / "people", "bob.json")
    _w(root / "people", "index.json")
    _w(root / "people", "duplicate_report.json")
    _w(root / "people", ".hidden.json")
    # places: 1
    _w(root / "places", "paris.json")
    # people_groups: 1 real + analysis/dedup artifacts that must NOT be counted (WARNING-2)
    _w(root / "people_groups", "first_army.json")
    _w(root / "people_groups", "related_groups_report.json")
    _w(root / "people_groups", "not_related.json")
    _w(root / "people_groups", "coords.json")
    # events live under content/<book>/*-event.json
    _w(root / "content" / "XChannel", "chapter1-event.json")
    _w(root / "content" / "XChannel", "chapter2-event.json")
    _w(root / "content" / "XChannel", "chapter1-parsed.json")  # not an event file

    counts = _log_phase2_summary(root, logging.getLogger("t"))

    assert counts["people"] == 2  # index/duplicate/hidden excluded
    assert counts["places"] == 1
    assert counts["people_groups"] == 1  # report/not_*/coords excluded (WARNING-2 fix)
    assert counts["events"] == 2  # only *-event.json
    # 'supplemental' is structurally always 0 (WARNING-1) — must not be a counted key at all
    assert "supplemental" not in counts
    # untouched optional types report 0 (the whole point — a 0 is visible)
    assert counts["casualties"] == 0
    assert counts["weather"] == 0


def test_core_zero_warns_optional_zero_is_info(tmp_path, caplog):
    # A run with NO core entities at all -> a LOUD core zero WARNING; optional zeros only at INFO.
    root = tmp_path / "output"
    _w(root / "maps", "placeholder.json")  # one optional type non-zero
    with caplog.at_level(logging.INFO):
        _log_phase2_summary(root, logging.getLogger("t"))
    warns = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("scope=core" in m and "events" in m for m in warns)  # core zero is LOUD
    assert any("scope=optional" in m for m in infos)  # optional zero is quiet
    # maps had a record, so it must NOT appear in the optional-zero list
    assert all("maps" not in m for m in infos if "scope=optional" in m)


def test_count_entity_dir_missing_is_zero(tmp_path):
    assert _count_entity_dir(tmp_path / "nope") == 0


def test_summary_failsafe_on_bad_root(caplog):
    # A non-path-like root must not raise — summary must never break the run.
    with caplog.at_level(logging.WARNING):
        counts = _log_phase2_summary(None, logging.getLogger("t"))
    assert counts == {}  # returns empty, no exception


# --- "0 because it failed" vs "0, nothing in the text" (XChannel-8, 2026-11-07) --------------


@pytest.fixture
def optional_errors():
    import phase2_extract as P

    P._OPTIONAL_ERRORS.clear()
    yield P
    P._OPTIONAL_ERRORS.clear()


def test_an_optional_zero_caused_by_errors_is_an_actionable_error(
    tmp_path, caplog, optional_errors
):
    P = optional_errors
    P._observe_optional_error(
        logging.getLogger("t"), "weather", "ch1-event.json", "boom"
    )
    with caplog.at_level(logging.INFO):
        _log_phase2_summary(tmp_path / "output", logging.getLogger("t"))
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    failed = [m.replace("_", " ") for m in errors if "phase2.optional_failed" in m]
    assert len(failed) == 1 and "weather" in failed[0] and "re-run Phase 2" in failed[0]
    quiet = [m for m in caplog.records if "scope=optional" in m.getMessage()]
    assert all("weather" not in r.getMessage().split("(")[0] for r in quiet)


def test_records_plus_errors_is_partial(tmp_path, caplog, optional_errors):
    P = optional_errors
    _w(tmp_path / "output" / "weather", "w.json")
    P._observe_optional_error(
        logging.getLogger("t"), "weather", "ch2-event.json", "boom"
    )
    with caplog.at_level(logging.INFO):
        _log_phase2_summary(tmp_path / "output", logging.getLogger("t"))
    assert any("phase2.optional_partial" in r.getMessage() for r in caplog.records)
    assert not any("phase2.optional_failed" in r.getMessage() for r in caplog.records)
