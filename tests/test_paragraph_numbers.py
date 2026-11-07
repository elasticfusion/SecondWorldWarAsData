"""LLM paragraph numbers are normalised to List[int] in one place (src.utils.paragraph_numbers).

XChannel-8 dev E2E (2026-11-06): one equipment broad item with string paragraph numbers crashed
``n - 1`` (TypeError: str - int) and zeroed the event's equipment (36 expected, 0 produced), halting
Phase 2 on the yield guard.
"""

import logging

import pytest

from src.utils.paragraph_numbers import coerce_paragraph_numbers


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([12, 13], [12, 13]),
        (["12", "13"], [12, 13]),
        ("12", [12]),
        (12, [12]),
        (["Paragraph_12", "paragraph 13"], [12, 13]),
        ([12.0], [12]),
        ([12, "12", 12], [12]),  # de-duplicated, order kept
        (None, []),
        ([], []),
        ("", []),
    ],
)
def test_readable_shapes(raw, expected):
    assert coerce_paragraph_numbers(raw, scope="t") == expected


@pytest.mark.parametrize(
    "bad",
    [
        ["twelve"],
        [True],
        [0],
        [-3],
        [12.5],
        [None],
        [{}],
        ["9" * 5000],  # Python's int-conversion limit raised ValueError here
        ["1234567890"],  # > 9 digits is not a paragraph number
        ["\u0661\u0662"],  # Arabic-Indic digits: a guess, so dropped
        ["\uff11\uff12"],  # fullwidth digits
        ["12, 13"],
        ["12-15"],
        [float("inf")],
        [float("nan")],
    ],
)
def test_unreadable_values_are_dropped_never_guessed(bad):
    assert coerce_paragraph_numbers(bad, scope="t") == []


def test_drop_is_reported_once_with_sample(caplog):
    caplog.set_level(logging.WARNING)
    assert coerce_paragraph_numbers([12, "x", "y"], scope="equipment.broad") == [12]
    lines = [
        r.message for r in caplog.records if "OBSERV paragraphs.unreadable" in r.message
    ]
    assert len(lines) == 1
    assert "dropped=2" in lines[0] and "kept=1" in lines[0]


def test_clean_input_is_silent(caplog):
    caplog.set_level(logging.WARNING)
    coerce_paragraph_numbers([1, 2], scope="t")
    assert not [r for r in caplog.records if "paragraphs.unreadable" in r.message]


# --------------------------------------------------------------------------- the crash site


def _event():
    return {
        "Event": {
            "EventID": "EV000100000000000000000000",
            "Sub-events": [
                {
                    "Sub-eventID": "SE000100000000000000000000",
                    "Sub-event_fulltext": {
                        "Paragraph_11": "Before.",
                        "Paragraph_12": "The LCTs carried DD tanks to Omaha.",
                        "Paragraph_13": "After.",
                    },
                }
            ],
        }
    }


@pytest.mark.parametrize("paras", [["12"], "12", 12, ["Paragraph_12"]])
def test_equipment_refine_tolerates_llm_paragraph_shapes(monkeypatch, paras):
    """The exact crash: a broad item with non-int paragraph numbers must refine, with the
    3-paragraph verbatim window and int provenance, instead of raising."""
    import src.extraction.equipment as EQ

    monkeypatch.setattr(EQ, "_call_refine", lambda *a, **k: None)  # no LLM
    item = {"name": "DD tank", "original_text": "DD tanks", "paragraph_numbers": paras}
    rec = EQ._refine_equipment_item(item, _event(), grok_client=None)
    assert rec["paragraph_numbers"] == [12]
    assert rec["extended_text"] == (
        "Before.\n\nThe LCTs carried DD tanks to Omaha.\n\nAfter."
    )


def test_events_fulltext_accepts_string_numbers():
    import src.extraction.events as EV

    se = {"paragraph_numbers": ["12", 13], "Sub-event_summary": "s"}
    EV._build_subevent_fulltext(se, {12: "twelve", 13: "thirteen"})
    assert se["Sub-event_fulltext"] == {
        "Paragraph_12": "twelve",
        "Paragraph_13": "thirteen",
    }


def test_large_clean_input_is_fast():
    """Dedup is set-based: 50k numbers must not be O(n^2)."""
    import time

    t0 = time.monotonic()
    out = coerce_paragraph_numbers(list(range(1, 50001)) * 2, scope="t")
    assert len(out) == 50000 and time.monotonic() - t0 < 2


def test_repeats_warn_once_then_count(caplog):
    from src.utils import observe

    observe.reset_counted_warnings()
    caplog.set_level(logging.WARNING)
    for _ in range(30):
        coerce_paragraph_numbers(["x"], scope="equipment.broad")
    lines = [
        r.message for r in caplog.records if "OBSERV paragraphs.unreadable" in r.message
    ]
    assert len(lines) == 2  # first, then the 25th
    assert "count=1" in lines[0] and "count=25" in lines[1]
    assert all("check_the_prompt/model" in m for m in lines)  # actionable


def test_events_validator_counts_string_numbers():
    """Behaviour change: a response whose paragraph_numbers are all strings used to cite NOTHING and
    skip range validation; now they count, so a hallucinated set is rejected."""
    import src.extraction.events as EV

    resp = {"Event": {"Sub-events": [{"paragraph_numbers": ["12", "Paragraph_13"]}]}}
    assert EV._cited_paragraph_numbers(resp) == [12, 13]


def test_refine_prefers_normalised_refine_numbers(monkeypatch):
    import src.extraction.equipment as EQ

    ev = {
        "Event": {
            "EventID": "EV000100000000000000000000",
            "Sub-events": [
                {"Sub-event_fulltext": {"Paragraph_12": "a", "Paragraph_13": "b"}}
            ],
        }
    }
    monkeypatch.setattr(
        EQ,
        "_call_refine",
        lambda *a, **k: {
            "common_name": "DD tank",
            "paragraph_numbers": ["Paragraph_13"],
        },
    )
    rec = EQ._refine_equipment_item(
        {"name": "DD tank", "original_text": "x", "paragraph_numbers": ["12"]}, ev, None
    )
    assert rec["paragraph_numbers"] == [13]
