"""Paragraph keys are zero-padded ("Paragraph_007") so text order is paragraph order: with
"Paragraph_10" sorting before "Paragraph_9", a sub-event's paragraphs reached the model out of
order. Every reader parses the number, so older unpadded event files keep working."""

from src.extraction import batch_parallel as bp
from src.extraction import casualties, equipment, events, images
from src.utils.paragraph_numbers import (
    in_paragraph_order,
    paragraph_key,
    paragraph_number,
)


def test_keys_are_zero_padded_and_sort_in_paragraph_order():
    assert paragraph_key(7) == "Paragraph_007"
    assert paragraph_key(84) == "Paragraph_084"
    assert paragraph_key(100) == "Paragraph_100"
    assert paragraph_key(1234) == "Paragraph_1234"  # past 999: longer, still parsed
    keys = [paragraph_key(n) for n in (100, 9, 99, 10, 1)]
    assert sorted(keys) == [paragraph_key(n) for n in (1, 9, 10, 99, 100)]


def test_padded_and_old_unpadded_keys_parse_to_the_same_number():
    assert paragraph_number("Paragraph_007") == paragraph_number("Paragraph_7") == 7
    assert paragraph_number("summary") is None


def test_mixed_old_and_new_keys_come_out_in_paragraph_order():
    fulltext = {
        "Paragraph_10": "ten",
        "Paragraph_009": "nine",
        "Paragraph_100": "hundred",
        "Paragraph_2": "two",
        "summary": "s",
    }
    assert [t for _k, t in in_paragraph_order(fulltext)] == [
        "two",
        "nine",
        "ten",
        "hundred",
        "s",
    ]


def test_sub_event_text_is_built_with_padded_keys():
    se = {"paragraph_numbers": [10, 9]}
    events._build_subevent_fulltext(se, {9: "nine", 10: "ten"})
    assert list(se["Sub-event_fulltext"]) == ["Paragraph_010", "Paragraph_009"]


def test_the_model_reads_a_sub_events_paragraphs_in_order():
    se = {
        "Sub-eventID": "S1",
        "Sub-event_summary": "x",
        "Sub-event_fulltext": {
            "Paragraph_100": "third-para",
            "Paragraph_99": "second-para",
            "Paragraph_9": "first-para",
        },
    }
    block = bp._build_source_block([se], include_fulltext=True)
    assert (
        block.index("first-para")
        < block.index("second-para")
        < block.index("third-para")
    )


def test_readers_accept_both_key_forms():
    old = {
        "Sub-eventID": "OLD",
        "Sub-event_fulltext": {"Paragraph_12": "a", "Paragraph_9": "b"},
    }
    new = {"Sub-eventID": "NEW", "Sub-event_fulltext": {"Paragraph_030": "c"}}
    ev = {"Event": {"Sub-events": [old, new]}}
    assert images._sub_event_containing([old, new], 30)["Sub-eventID"] == "NEW"
    assert images._sub_event_containing([old, new], 9)["Sub-eventID"] == "OLD"
    assert equipment._find_sub_event(ev, [30])["Sub-eventID"] == "NEW"
    assert equipment._paragraph_lookup(ev) == {12: "a", 9: "b", 30: "c"}
    assert casualties._first_paragraph_number(old) == 9  # lowest, not the first key
