"""Every paragraph belongs to ONE sub-event (XChannel-8, 2026-11-07).

The events response put Paragraph_39-262 in "U.S. airborne assault" and Paragraph_124-262 in
"Seaborne beach landings": 105 paragraphs were sent to Grok twice for every entity type and every
entity in them was attributed to both sub-events.
"""

import logging

from src.extraction import events as EV


def _resp(*claims, key="paragraph_numbers"):
    return {
        "Event": {
            "Sub-events": [
                {"Sub-event_summary": f"S{i}", key: list(c)}
                for i, c in enumerate(claims)
            ]
        }
    }


def _nums(resp):
    return [se["paragraph_numbers"] for se in resp["Event"]["Sub-events"]]


def test_the_xchannel_8_overlap_is_split_at_the_smaller_claim(caplog):
    resp = _resp(
        range(3, 15), range(15, 27), range(27, 39), range(39, 263), range(124, 263)
    )
    with caplog.at_level(logging.INFO):
        assert EV._make_paragraphs_disjoint(resp) == 139
    airborne, beaches = _nums(resp)[3], _nums(resp)[4]
    assert airborne == list(range(39, 124)) and beaches == list(range(124, 263))
    assert any("paragraph_overlap_resolved" in r.getMessage() for r in caplog.records)


def test_every_paragraph_is_owned_once_and_none_is_lost():
    resp = _resp([1, 2, 3, 4], [3, 4, 5], [5, 6])
    EV._make_paragraphs_disjoint(resp)
    owned = [n for nums in _nums(resp) for n in nums]
    assert sorted(owned) == [1, 2, 3, 4, 5, 6] and len(owned) == len(set(owned))


def test_a_tie_goes_to_the_earlier_sub_event():
    resp = _resp([1, 2], [2, 3])
    EV._make_paragraphs_disjoint(resp)
    assert _nums(resp) == [[1, 2], [3]]


def test_disjoint_input_is_unchanged_and_silent(caplog):
    resp = _resp([1, 2], [3, 4])
    with caplog.at_level(logging.INFO):
        assert EV._make_paragraphs_disjoint(resp) == 0
    assert _nums(resp) == [[1, 2], [3, 4]]
    assert not any("paragraph_overlap" in r.getMessage() for r in caplog.records)


def test_the_older_paragraphs_key_and_string_numbers_are_handled():
    resp = _resp(["1", "2", "3"], [2], key="paragraphs")
    EV._make_paragraphs_disjoint(resp)
    subs = resp["Event"]["Sub-events"]
    assert "paragraphs" not in subs[0]
    assert _nums(resp) == [[1, 3], [2]]


def test_a_fully_contained_sub_event_keeps_its_paragraphs_and_the_container_loses_them():
    resp = _resp(range(1, 11), [4, 5])
    EV._make_paragraphs_disjoint(resp)
    assert _nums(resp) == [[1, 2, 3, 6, 7, 8, 9, 10], [4, 5]]


def test_a_sub_event_with_fulltext_and_no_numbers_is_left_alone():
    resp = {"Event": {"Sub-events": [{"Sub-event_fulltext": {"Paragraph_1": "x"}}]}}
    assert EV._make_paragraphs_disjoint(resp) == 0
    assert "paragraph_numbers" not in resp["Event"]["Sub-events"][0]


def test_references_found_only_in_the_lost_paragraphs_move_with_them():
    parsed = {
        "paragraphs": [
            {"absolute_number": 1, "text": "Drop zones.<sup>[5](x)</sup>"},
            {"absolute_number": 2, "text": "Beaches.<sup>[9](x)</sup>"},
        ]
    }
    resp = _resp([1, 2], [2])
    resp["Event"]["Sub-events"][0]["references"] = [5, 9]
    EV._make_paragraphs_disjoint(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["references"] == [5]


def test_the_normalizer_builds_disjoint_fulltext_and_footnotes():
    parsed = {
        "chapter_title": "C",
        "book": "B",
        "paragraphs": [
            {"absolute_number": n, "text": f"text {n}<sup>[{n}](x)</sup>"}
            for n in range(1, 7)
        ],
        "footnotes": [{"number": n, "kind": "footnote"} for n in range(1, 7)],
    }
    resp = _resp(range(1, 7), range(4, 7))
    out = EV._normalize_event_response(resp, parsed, None)
    a, b = out["Event"]["Sub-events"]
    assert set(a["Sub-event_fulltext"]) == {
        "Paragraph_001",
        "Paragraph_002",
        "Paragraph_003",
    }
    assert set(b["Sub-event_fulltext"]) == {
        "Paragraph_004",
        "Paragraph_005",
        "Paragraph_006",
    }
    assert not set(a.get("Footnote_References") or []) & set(
        b.get("Footnote_References") or []
    )


def test_a_sub_event_left_with_no_paragraph_is_reported_and_keeps_its_summary(caplog):
    resp = _resp([1, 2, 3], [1, 2], [3])
    with caplog.at_level(logging.INFO):
        EV._make_paragraphs_disjoint(resp)
    assert _nums(resp) == [[], [1, 2], [3]]
    assert any("emptied=1" in r.getMessage() for r in caplog.records)
    se = resp["Event"]["Sub-events"][0]
    EV._build_subevent_fulltext(se, {1: "a", 2: "b", 3: "c"})
    assert se["Sub-event_fulltext"] == {"summary": "S0"}  # never dropped


def test_two_emptied_sub_events_with_the_same_summary_get_distinct_ids():
    event = {
        "Sub-events": [
            {
                "Sub-event_summary": "Landing",
                "Sub-event_fulltext": {"summary": "Landing"},
            },
            {
                "Sub-event_summary": "Landing",
                "Sub-event_fulltext": {"summary": "Landing"},
            },
        ]
    }
    EV._assign_subevent_ids(event, "01M6W8ZCHP9A3NYBQSXBHDN9G4")
    ids = [se["Sub-eventID"] for se in event["Sub-events"]]
    assert len(set(ids)) == 2


def test_a_null_summary_does_not_break_the_pass():
    resp = {
        "Event": {
            "Sub-events": [
                {"Sub-event_summary": None, "paragraph_numbers": [1]},
                {"Sub-event_summary": "B", "paragraph_numbers": [1]},
            ]
        }
    }
    EV._make_paragraphs_disjoint(resp)  # must not raise
    assert _nums(resp) == [[1], []]


def test_a_cited_number_that_was_not_given_cannot_steal_a_paragraph():
    parsed = {"paragraphs": [{"absolute_number": n, "text": "t"} for n in (1, 2, 3)]}
    # sub-event 1 claims [2, 98, 99]: only [2] is real, so its claim size is 1
    resp = _resp([1, 2], [2, 98, 99], [3])
    EV._make_paragraphs_disjoint(resp, parsed)
    assert _nums(resp) == [[1], [2], [3]]


def test_non_ascii_digit_references_are_kept_not_crashed():
    parsed = {
        "paragraphs": [
            {"absolute_number": 1, "text": "a<sup>[5](x)</sup>"},
            {"absolute_number": 2, "text": "b"},
        ]
    }
    resp = _resp([1, 2], [1])
    resp["Event"]["Sub-events"][0]["references"] = ["²", 5]
    EV._make_paragraphs_disjoint(resp, parsed)
    assert resp["Event"]["Sub-events"][0]["references"] == ["²"]
