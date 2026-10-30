"""Events validate-and-resubmit (user 2026-10-30): a Grok events response whose paragraph_numbers
fall mostly outside the input range (absolute_number 1..N) is a hallucination that would starve all
downstream entity extraction (people=0/places=0 fail_stop). Reject + retry before proceeding.
"""

import pytest
from jsonschema import ValidationError

from src.extraction.events import _validate_paragraph_range

_PARSED = {
    "paragraphs": [{"absolute_number": str(i), "text": "t"} for i in range(1, 264)]
}  # 1..263


def test_out_of_range_response_rejected():
    bad = {"Event": {"Sub-events": [{"paragraph_numbers": list(range(264, 365))}]}}
    with pytest.raises(ValidationError):
        _validate_paragraph_range(bad, _PARSED, 0)


def test_in_range_response_passes():
    good = {"Event": {"Sub-events": [{"paragraph_numbers": [1, 2, 3, 50, 263]}]}}
    _validate_paragraph_range(good, _PARSED, 0)  # must not raise


def test_few_stray_refs_tolerated():
    mostly = {
        "Event": {"Sub-events": [{"paragraph_numbers": [1, 2, 3, 4, 999]}]}
    }  # 1/5 out
    _validate_paragraph_range(mostly, _PARSED, 0)  # must not raise


def test_no_baseline_or_no_nums_is_noop():
    _validate_paragraph_range({"Event": {"Sub-events": []}}, _PARSED, 0)  # no nums
    _validate_paragraph_range(
        {"Event": {"Sub-events": [{"paragraph_numbers": [1]}]}}, {"paragraphs": []}, 0
    )  # no baseline
