"""Origin normalization (USSR/Soviet/Russia -> SUN) so dedup's origin veto compares
consistent codes; and the T-34 (Soviet) vs T34 Calliope (US) collision stays separate when
origin is complete + consistently coded."""

from src.extraction.equipment import _normalize_origin
from scripts.find_duplicate_equipment import _score_pair


def test_soviet_variants_normalize_to_sun():
    for v in ("USSR", "Soviet", "Soviet Union", "Russia", "Russian", "sun"):
        assert _normalize_origin(v) == "SUN", v


def test_valid_codes_passthrough():
    assert _normalize_origin("USA") == "USA"
    assert _normalize_origin("DEU") == "DEU"
    assert _normalize_origin(None) is None


def test_t34_soviet_vs_t34_calliope_us_not_merged():
    # Same-ish leading token, DIFFERENT origin -> origin veto keeps them separate.
    soviet = {
        "common_name": "T-34",
        "country_of_origin": "SUN",
        "category": "armor",
        "event_mentions": [{"MentionID": "01A"}],
    }
    us = {
        "common_name": "T34 Calliope",
        "country_of_origin": "USA",
        "category": "armor",
        "event_mentions": [{"MentionID": "01B"}],
    }
    conf, _, _ = _score_pair(soviet, us)
    assert conf == 0.0  # vetoed on origin — no collision when origin is complete+coded


def test_two_soviet_t34_consistent_origin_can_match():
    # Both SUN (consistently coded) -> origin veto does NOT fire -> they can match.
    a = {
        "common_name": "T-34",
        "country_of_origin": "SUN",
        "category": "armor",
        "event_mentions": [{"MentionID": "01A"}],
    }
    b = {
        "common_name": "T-34",
        "country_of_origin": "SUN",
        "category": "armor",
        "event_mentions": [{"MentionID": "01B"}],
    }
    conf, _, _ = _score_pair(a, b)
    assert conf > 0.0
