"""Equipment origin-vs-operator model: dedup veto keys on country_of_origin (design/
manufacture origin, stable identity) ONLY — never on who was using it. British-used US
Shermans and German-captured US gear must NOT be split."""

from scripts.find_duplicate_equipment import _score_pair, _any_captured


def _eq(name, origin, operating=None, captured=False, category="armor"):
    m = {"MentionID": "01X"}
    if operating:
        m["operating_country"] = operating
    if captured:
        m["captured"] = True
    return {
        "common_name": name,
        "country_of_origin": origin,
        "category": category,
        "event_mentions": [m],
    }


def test_british_used_us_sherman_not_vetoed():
    # Same equipment TYPE (US-origin M4), different operators -> must NOT veto.
    us = _eq("M4 Sherman", "USA", operating="USA")
    gb = _eq("M4 Sherman", "USA", operating="GBR")
    conf, _, _ = _score_pair(us, gb)
    assert conf > 0.0, "British-used US Sherman must stay one record (not vetoed)"


def test_german_captured_us_m10_not_vetoed():
    # Origin USA either way; one mention operated by Germany, captured=true.
    a = _eq("M10", "USA", operating="USA")
    b = _eq("M10", "USA", operating="DEU", captured=True)
    conf, _, _ = _score_pair(a, b)
    assert conf > 0.0, "German-captured US M10 must not be split from the US M10 record"


def test_genuine_origin_mismatch_still_vetoes():
    # Truly different origins (US Sherman vs German Panther) -> veto stands.
    us = _eq("M4 Sherman", "USA")
    de = _eq("Panther", "DEU")
    conf, _, _ = _score_pair(us, de)
    assert conf == 0.0, "genuinely different origins must still veto"


def test_operator_difference_never_vetoes_same_origin():
    # Operator differs but origin identical -> operator must be irrelevant to the veto.
    a = _eq("M4 Sherman", "USA", operating="GBR")
    b = _eq("M4 Sherman", "USA", operating="USA")
    conf, _, _ = _score_pair(a, b)
    assert conf > 0.0


def test_any_captured_reads_structured_flag():
    assert _any_captured(_eq("M10", "USA", operating="DEU", captured=True)) is True
    assert _any_captured(_eq("M10", "USA", operating="USA")) is False
