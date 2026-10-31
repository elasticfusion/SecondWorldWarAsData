"""Regression: fuzzy equipment matching must NOT merge distinct calibers/designations
(105-mm vs 155-mm howitzer merged at ratio 0.93 — the XChannel-8 7->3 undercount loss),
while still merging legitimate shared-number variants (M4 / M4A1)."""

from pathlib import Path
from src.extraction.equipment import _fuzzy_match_equipment, _equipment_numeric_conflict


def test_numeric_conflict_detects_different_calibers():
    assert _equipment_numeric_conflict("105-mm howitzer", "155-mm howitzer") is True
    assert _equipment_numeric_conflict("150-mm gun", "155-mm gun") is True


def test_no_conflict_when_numbers_shared_or_absent():
    assert (
        _equipment_numeric_conflict("M4 Sherman", "M4A1 Sherman") is False
    )  # share '4'
    assert (
        _equipment_numeric_conflict("Tommy gun", "machine gun") is False
    )  # no numbers
    assert _equipment_numeric_conflict("8-inch gun", "gun") is False  # one has none


def test_fuzzy_does_not_merge_different_calibers():
    idx = {"155-mm howitzer": Path("/tmp/x.json")}
    # 105-mm howitzer is 0.93 similar but a DIFFERENT weapon -> must NOT match
    assert _fuzzy_match_equipment("105-mm howitzer", idx) is None


def test_fuzzy_still_merges_shared_number_variant():
    idx = {"M4 Sherman": Path("/tmp/m4.json")}
    # M4A1 Sherman (0.91) shares '4' -> legitimate variant -> still matches
    assert _fuzzy_match_equipment("M4A1 Sherman", idx) == "M4 Sherman"
