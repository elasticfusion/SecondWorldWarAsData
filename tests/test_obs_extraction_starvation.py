"""PR-B: extraction-starvation telemetry (empty-fulltext skip -> WARNING + all-empty alarm).

These assert the DIRECT starvation signature of the 2026-10-28 paragraph_numbers incident now fires
at WARNING (not swallowed at DEBUG), and that a whole-event empty-fulltext emits ONE loud alarm —
while remaining strictly fail-safe (telemetry errors never crash extraction).
"""

import logging

from src.extraction import starvation_signal as S
from src.extraction.places import create_place_prompt
from src.extraction.dates import create_date_prompt
from src.extraction.people import create_people_prompt
from src.extraction.weather_central import create_weather_prompt


def test_note_empty_fulltext_skip_warns_and_counts(caplog):
    counter = {"empty": 0}
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        S.note_empty_fulltext_skip("places", "SE1", counter)
    assert "OBSERV places.empty_fulltext_skip" in caplog.text
    assert "SE1" in caplog.text
    assert counter["empty"] == 1


def test_note_empty_fulltext_skip_fail_safe_on_bad_counter():
    # A non-dict counter must not raise (fail-safe).
    S.note_empty_fulltext_skip("people", "SE2", counter=None)
    S.note_empty_fulltext_skip("people", "SE2", counter=["bad"])  # type: ignore[arg-type]


def test_warn_all_subevents_empty_fires_when_all_empty(caplog):
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        S.warn_all_subevents_empty("people", "E9", sub_events=5, empty=5)
    assert "OBSERV people.all_subevents_empty" in caplog.text
    assert "event=E9" in caplog.text


def test_warn_all_subevents_empty_silent_when_partial(caplog):
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        S.warn_all_subevents_empty("people", "E9", sub_events=5, empty=2)
    assert "all_subevents_empty" not in caplog.text


def test_warn_all_subevents_empty_silent_when_no_subevents(caplog):
    # 0 sub-events -> legitimately nothing to extract, NOT an alarm.
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        S.warn_all_subevents_empty("places", "E0", sub_events=0, empty=0)
    assert "all_subevents_empty" not in caplog.text


# --- Each prompt builder raises the empty skip to WARNING via the shared helper ---------------

_EMPTY_SE = {
    "Sub-eventID": "01EMPTY",
    "Sub-event_summary": "",
    "Sub-event_fulltext": {},
}


def test_places_prompt_empty_warns_and_counts(caplog):
    counter = {"empty": 0}
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        out = create_place_prompt(_EMPTY_SE, "E1", "Chapter", empty_counter=counter)
    assert out == ""
    assert "OBSERV places.empty_fulltext_skip" in caplog.text
    assert counter["empty"] == 1


def test_people_prompt_empty_warns_and_counts(caplog):
    counter = {"empty": 0}
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        out = create_people_prompt(_EMPTY_SE, "E1", "Chapter", empty_counter=counter)
    assert out == ""
    assert "OBSERV people.empty_fulltext_skip" in caplog.text
    assert counter["empty"] == 1


def test_dates_prompt_empty_warns_and_counts(caplog):
    counter = {"empty": 0}
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        out = create_date_prompt(_EMPTY_SE, "E1", "Chapter", empty_counter=counter)
    assert out == ""
    assert "OBSERV dates.empty_fulltext_skip" in caplog.text
    assert counter["empty"] == 1


def test_weather_prompt_empty_warns_and_counts(caplog):
    counter = {"empty": 0}
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        out = create_weather_prompt(
            _EMPTY_SE, "E1", "Chapter", places_index={}, empty_counter=counter
        )
    assert out == ""
    assert "OBSERV weather.empty_fulltext_skip" in caplog.text
    assert counter["empty"] == 1


def test_prompt_builders_nonempty_do_not_warn(caplog):
    se = {
        "Sub-eventID": "01OK",
        "Sub-event_summary": "s",
        "Sub-event_fulltext": {"p1": "General Patton advanced on Metz."},
    }
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        assert create_place_prompt(se, "E1", "C") != ""
        assert create_people_prompt(se, "E1", "C") != ""
        assert create_weather_prompt(se, "E1", "C", places_index={}) != ""
    assert "empty_fulltext_skip" not in caplog.text
