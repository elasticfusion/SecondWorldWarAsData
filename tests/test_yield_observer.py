"""Expected-yield observability helper (PR-A enabler)."""

import logging
from src.utils import yield_observer as Y


def test_classify_starvation():
    assert (
        Y._classify_yield(sub_events=6, fulltext_nonempty=0, produced=0) == "starvation"
    )


def test_classify_regression():
    assert (
        Y._classify_yield(sub_events=6, fulltext_nonempty=6, produced=0) == "regression"
    )


def test_classify_ok_healthy():
    assert Y._classify_yield(sub_events=6, fulltext_nonempty=6, produced=12) == "ok"


def test_classify_ok_genuinely_empty():
    # no sub-events at all -> 0 produced is legitimately empty, NOT an alarm
    assert Y._classify_yield(sub_events=0, fulltext_nonempty=0, produced=0) == "ok"


def test_count_nonempty_real_paragraphs():
    ev = {
        "Event": {
            "Sub-events": [
                {
                    "Sub-eventID": "a",
                    "Sub-event_fulltext": {
                        "Paragraph_3": "real text",
                        "Paragraph_5": "more",
                    },
                },
                {
                    "Sub-eventID": "b",
                    "Sub-event_fulltext": {"summary": "just a summary"},
                },  # fallback -> NOT counted
                {"Sub-eventID": "c", "Sub-event_fulltext": {}},  # empty -> not counted
            ]
        }
    }
    assert Y.count_nonempty_fulltext(ev) == 1


def test_observe_yield_warns_on_starvation(caplog):
    with caplog.at_level(logging.WARNING, logger=Y.__name__):
        v = Y.observe_yield(
            "equipment", "E1", sub_events=6, fulltext_nonempty=0, produced=0
        )
    assert v == "starvation"
    assert (
        "OBSERV yield.equipment" in caplog.text and "verdict=starvation" in caplog.text
    )


def test_observe_yield_warns_on_regression(caplog):
    with caplog.at_level(logging.WARNING, logger=Y.__name__):
        v = Y.observe_yield(
            "places", "E1", sub_events=6, fulltext_nonempty=6, produced=0
        )
    assert v == "regression"
    assert "verdict=regression" in caplog.text


def test_observe_yield_info_on_ok(caplog):
    with caplog.at_level(logging.INFO, logger=Y.__name__):
        v = Y.observe_yield(
            "places", "E1", sub_events=6, fulltext_nonempty=6, produced=7
        )
    assert v == "ok"
    assert "verdict=ok" in caplog.text


def test_observe_yield_never_raises():
    # hostile args must not raise
    assert Y.observe_yield("x", None, None, None, None) in (
        "ok",
        "starvation",
        "regression",
    )
