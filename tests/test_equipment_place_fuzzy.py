"""Fuzzy PlaceID resolution: exact -> containment -> conservative SequenceMatcher, with a
guard against false matches."""

from src.extraction.equipment import _resolve_place_id

IDX = {
    "cherbourg": "01CHERBOURG0000000000000000",
    "saint-lô": "01SAINTLO000000000000000000",
    "carentan": "01CARENTAN00000000000000000",
}


def test_exact_match():
    assert _resolve_place_id("Cherbourg", IDX) == "01CHERBOURG0000000000000000"


def test_containment_phrase():
    # "the crossroads in Cherbourg" contains the index key "cherbourg"
    assert (
        _resolve_place_id("the crossroads in Cherbourg", IDX)
        == "01CHERBOURG0000000000000000"
    )


def test_containment_longest_key_wins():
    idx = dict(IDX, **{"saint-lô crossroads": "01SLXROADS0000000000000000"})
    assert (
        _resolve_place_id("near the Saint-Lô Crossroads", idx)
        == "01SLXROADS0000000000000000"
    )


def test_fuzzy_ratio_typo():
    # minor typo -> high ratio -> resolves
    assert _resolve_place_id("Cherbourge", IDX) == "01CHERBOURG0000000000000000"


def test_false_match_rejected():
    # a genuinely different place that is NOT contained and below the ratio threshold
    assert _resolve_place_id("Bastogne", IDX) is None


def test_empty_inputs():
    assert _resolve_place_id("", IDX) is None
    assert _resolve_place_id("Cherbourg", None) is None
