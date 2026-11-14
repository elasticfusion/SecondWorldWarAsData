"""A narrative paragraph the events answer leaves out is given, deterministically, to the nearest
sub-event (Cross-Channel Attack 2026-11-14: every chapter's heading lines, and chapter 10's closing
four paragraphs - their entities were dropped as unattributable)."""

from src.extraction import events as E


def _parsed(n, size=100):
    return {
        "paragraphs": [
            {"absolute_number": i, "text": ("# Title" if i == 1 else "x" * size)}
            for i in range(1, n + 1)
        ]
    }


def _response(*claims):
    return {"Event": {"Sub-events": [{"paragraph_numbers": list(c)} for c in claims]}}


def test_leading_and_trailing_paragraphs_go_to_the_nearest_sub_event():
    r = _response(range(3, 8), range(8, 19))
    assert E._claim_orphan_paragraphs(r, _parsed(20)) == 4
    subs = r["Event"]["Sub-events"]
    assert subs[0]["paragraph_numbers"] == list(
        range(1, 8)
    )  # the heading lines: the first
    assert subs[1]["paragraph_numbers"] == list(
        range(8, 21)
    )  # the closing ones: the last


def test_a_gap_goes_to_the_sub_event_before_it():
    r = _response(range(1, 10), range(11, 21))
    E._claim_orphan_paragraphs(r, _parsed(20))
    assert r["Event"]["Sub-events"][0]["paragraph_numbers"] == list(range(1, 11))


def test_an_answer_holding_too_little_is_left_for_the_coverage_retry():
    r = _response([1, 2], [3])
    assert E._claim_orphan_paragraphs(r, _parsed(20)) == 0
    assert r["Event"]["Sub-events"][1]["paragraph_numbers"] == [3]


def test_footnotes_are_not_claimed():
    parsed = _parsed(6)
    parsed["paragraphs"][4]["text"] = "### Footnotes"
    parsed["paragraphs"][5]["text"] = "[1] A note."
    r = _response([1, 2, 3, 4])
    assert E._claim_orphan_paragraphs(r, parsed) == 0


def test_claimed_paragraphs_reach_the_fulltext():
    parsed = _parsed(6)
    r = _response([2, 3, 4, 5, 6])
    r["Event"]["Sub-events"][0]["Sub-event_summary"] = "s"
    E._claim_orphan_paragraphs(r, parsed)
    E._reconstruct_fulltext(r, parsed)
    assert sorted(r["Event"]["Sub-events"][0]["Sub-event_fulltext"]) == [
        "Paragraph_001",
        "Paragraph_002",
        "Paragraph_003",
        "Paragraph_004",
        "Paragraph_005",
        "Paragraph_006",
    ]
