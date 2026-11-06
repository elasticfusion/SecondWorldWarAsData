"""observe_warn_counted: warn on the first occurrence and every Nth, count the rest (alerts must be
actionable, not one line per item)."""

import logging

from src.utils import observe


def test_first_then_every_nth_with_counts(caplog, monkeypatch):
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)
    observe.reset_counted_warnings()
    caplog.set_level(logging.WARNING, logger="observe")
    for _ in range(10):
        observe.observe_warn_counted("t", "e", key=("k",), note="do X", every=4)
    lines = [r.message for r in caplog.records if "OBSERV t.e" in r.message]
    assert len(lines) == 3  # count 1, 4, 8
    assert "count=1" in lines[0] and "count=4" in lines[1] and "count=8" in lines[2]
    assert all("do_X" in m for m in lines)


def test_keys_are_independent_and_totals_reported():
    observe.reset_counted_warnings()
    observe.observe_warn_counted("t", "e", key=("a",), note="n")
    observe.observe_warn_counted("t", "e", key=("a",), note="n")
    observe.observe_warn_counted("t", "e", key=("b",), note="n")
    assert observe.counted_warning_totals("t", "e") == {("a",): 2, ("b",): 1}
    observe.reset_counted_warnings()
    assert observe.counted_warning_totals("t", "e") == {}


def test_never_raises_on_bad_key():
    observe.reset_counted_warnings()
    assert observe.observe_warn_counted("t", "e", key=None, note="n") == 0  # type: ignore[arg-type]
