"""Shared per-sub-event discovery (src/extraction/sub_events.py) and its synchronous user, the
equipment BROAD run. Chapter 8 equipment: 99 items asked chapter-wide, 358 / 331 / 317 asked one
sub-event at a time (2026-11-12)."""

import contextvars
from unittest import mock

import pytest

import src.grok_client as gc
from src.extraction import equipment as eq
from src.extraction import sub_events as per_sub_event
from src.utils.grok_models import GrokModelUnavailable

EVENT = {
    "Event": {
        "EventID": "E1",
        "Sub-events": [
            {
                "Sub-eventID": "S1",
                "Sub-event_fulltext": {
                    "Paragraph_1": "The LCTs beached.",
                    "Paragraph_2": "Two DD tanks sank.",
                },
            },
            {
                "Sub-eventID": "S2",
                "Sub-event_fulltext": {"Paragraph_3": "An 88 fired on the beach."},
            },
            {"Sub-eventID": "S3", "Sub-event_fulltext": {"Paragraph_4": "Quiet."}},
        ],
    }
}


def _sid(view):
    (se,) = view["Event"]["Sub-events"]
    return se["Sub-eventID"]


def test_each_call_sees_one_sub_event_and_results_keep_source_order():
    var = contextvars.ContextVar("book", default=None)
    var.set("B")
    got = per_sub_event.run_sync(lambda se, view: (_sid(view), var.get()), EVENT, "x")
    assert got == [
        ("S1", "B"),
        ("S2", "B"),
        ("S3", "B"),
    ]  # the book travels into the threads


def test_a_failed_sub_event_is_counted_and_alerted_and_the_rest_kept():
    def one(se, view):
        if se["Sub-eventID"] == "S2":
            raise ValueError("bad json")
        return se["Sub-eventID"]

    with mock.patch.object(per_sub_event, "observe_error") as err:
        got = per_sub_event.run_sync(one, EVENT, "equipment")
    assert got == ["S1", "S3"]
    assert err.call_args.args == ("equipment", "sub_events_failed")
    kw = err.call_args.kwargs
    assert kw["failed"] == 1 and kw["of"] == 3 and "S2: ValueError" in kw["errors"][0]


def test_a_model_stop_sends_nothing_more_and_is_raised():
    sent = []

    def one(se, view):
        sent.append(se["Sub-eventID"])
        raise GrokModelUnavailable("gone")

    with pytest.raises(GrokModelUnavailable):
        per_sub_event.run_sync(one, EVENT, "equipment", workers=1)
    assert sent == ["S1"]


def test_batch_collect_queues_every_sub_event_then_raises_once():
    queued = []

    def one(se, view):
        queued.append(se["Sub-eventID"])
        raise gc.BatchModeCollecting("queued")

    with pytest.raises(gc.BatchModeCollecting):
        per_sub_event.run_sync(one, EVENT, "equipment")
    assert sorted(queued) == ["S1", "S2", "S3"]


# --- equipment BROAD -----------------------------------------------------------------------------


class _Client:
    """Answers the BROAD prompt by the one sub-event it carries."""

    def __init__(self, answers):
        self.answers, self.keys = answers, []

    def _get_model(self, _kind):
        return "m"

    def extract_json(self, prompt, cache_key=None, **k):
        self.keys.append(cache_key)
        for sid, answer in self.answers.items():
            if f'"Sub-eventID": "{sid}"' in prompt:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError("prompt carried no known sub-event")


def test_equipment_broad_asks_each_sub_event_with_its_own_cache_key():
    client = _Client(
        {
            "S1": {
                "Equipment": [
                    {
                        "name": "LCT",
                        "original_text": "The LCTs beached.",
                        "paragraph_numbers": [1],
                    }
                ]
            },
            "S2": {
                "equipment": [
                    {
                        "name": "88",
                        "original_text": "An 88 fired",
                        "paragraph_numbers": ["3"],
                    }
                ]
            },
            "S3": [],
        }
    )
    items = eq._extract_equipment_broad(EVENT, client)
    assert sorted(i["name"] for i in items) == ["88", "LCT"]
    assert len(client.keys) == 3 and len(set(client.keys)) == 3


def test_an_item_is_pinned_to_the_sub_event_it_was_found_in():
    """A paragraph number outside the sub-event would attribute the mention elsewhere
    (_find_sub_event): keep the in-sub-event ones, else the paragraph quoting the item.
    """
    se = EVENT["Event"]["Sub-events"][0]
    stray = {
        "name": "DD tank",
        "original_text": "two  DD tanks",
        "paragraph_numbers": [3, 9],
    }
    assert eq._pin_to_sub_event(stray, se)["paragraph_numbers"] == [2]
    mixed = {"name": "LCT", "original_text": "x", "paragraph_numbers": [1, 3]}
    assert eq._pin_to_sub_event(mixed, se)["paragraph_numbers"] == [1]
    unquoted = {
        "name": "mine",
        "original_text": "not in the text",
        "paragraph_numbers": [],
    }
    assert eq._pin_to_sub_event(unquoted, se)["paragraph_numbers"] == [1, 2]
    found_in = eq._find_sub_event(
        EVENT, eq._pin_to_sub_event(stray, se)["paragraph_numbers"]
    )
    assert found_in["Sub-eventID"] == "S1"


def test_equipment_broad_keeps_the_other_sub_events_when_one_fails():
    client = _Client(
        {
            "S1": gc.GrokNoJsonError("prose"),
            "S2": [
                {"name": "88", "original_text": "An 88 fired", "paragraph_numbers": [3]}
            ],
            "S3": [],
        }
    )
    with (
        mock.patch.object(per_sub_event, "observe_error") as err,
        mock.patch.object(eq, "observe_error") as broad_err,
    ):
        items = eq._extract_equipment_broad(EVENT, client)
    assert [i["name"] for i in items] == ["88"]
    assert err.call_args.kwargs["failed"] == 1
    assert "S1: GrokNoJsonError" in err.call_args.kwargs["errors"][0]
    broad_err.assert_not_called()  # one alert per failure, not two


def test_a_sub_event_without_an_id_gets_its_own_cache_key():
    a = {"Sub-event_fulltext": {"Paragraph_1": "x"}}
    b = {"Sub-event_fulltext": {"Paragraph_2": "y"}}
    runs = {eq._broad_run(a), eq._broad_run(b), eq._broad_run({})}
    assert len(runs) == 3 and "broad" in runs  # "broad" stays the whole-event key


def test_an_event_without_sub_events_is_asked_whole():
    client = _Client({})
    client.extract_json = lambda prompt, cache_key=None, **k: [{"name": "LST"}]
    items = eq._extract_equipment_broad({"Event": {"EventID": "E"}}, client)
    assert [i["name"] for i in items] == ["LST"]


def test_falling_back_to_the_whole_sub_event_is_flagged():
    se = EVENT["Event"]["Sub-events"][0]
    with mock.patch.object(eq, "observe_warn") as warn:
        eq._pin_to_sub_event({"name": "mine", "original_text": "elsewhere"}, se)
    assert warn.call_args.args[1] == "pin_fallback_whole_sub_event"
