"""Config-driven people dedup weighting: defaults, radius decay, conflicting-initial
veto, and config override."""

import importlib.util
import sys
from pathlib import Path

from src.dedup.config import load_dedup_config

_SPEC = importlib.util.spec_from_file_location(
    "fdp_test", Path(__file__).parent.parent / "scripts" / "find_duplicate_people.py"
)
fdp = importlib.util.module_from_spec(_SPEC)
sys.modules["fdp_test"] = fdp
_SPEC.loader.exec_module(fdp)


def test_defaults_match_prior_behavior():
    cfg = load_dedup_config(None)
    assert cfg.candidate_threshold == 0.5
    # auto-merge stays effectively off by default (no new auto-merges beyond identical)
    assert cfg.auto_merge_threshold >= 100
    assert cfg.weight("same_last_name") == 0.4
    assert cfg.weight("shared_external_url") == 3.0


def test_absent_or_invalid_config_falls_back():
    cfg = load_dedup_config({"dedup": {"people": {"candidate_threshold": "oops"}}})
    assert cfg.candidate_threshold == 0.5  # invalid -> default


def test_config_override_applies():
    cfg = load_dedup_config(
        {"dedup": {"people": {"candidate_threshold": 1.2, "auto_merge_threshold": 2.0}}}
    )
    assert cfg.candidate_threshold == 1.2
    assert cfg.auto_merge_threshold == 2.0


def test_conflicting_middle_initial_detected():
    assert fdp._conflicting_middle_initial("George S. Patton", "George P. Patton")
    # one simply omits the middle -> NOT a conflict
    assert not fdp._conflicting_middle_initial("George Patton", "George S. Patton")
    # shared middle -> not a conflict
    assert not fdp._conflicting_middle_initial("George S. Patton", "George S. Patton")


def test_conflicting_middle_veto_is_negative():
    cfg = load_dedup_config(None)
    reasons, score = fdp._check_conflicting_middle(
        "George S. Patton", "George P. Patton", "patton", "patton", cfg
    )
    assert reasons and score < 0
    # different surname -> no veto
    r2, s2 = fdp._check_conflicting_middle(
        "A B Smith", "A C Jones", "smith", "jones", cfg
    )
    assert s2 == 0.0


def test_proximity_weight_decays_with_radius():
    cfg = load_dedup_config(None)
    tight = fdp._proximity_weight(10, cfg)
    near = fdp._proximity_weight(300, cfg)
    loose = fdp._proximity_weight(2000, cfg)
    far = fdp._proximity_weight(9000, cfg)
    assert tight > near > loose > 0
    assert far == 0.0
    assert fdp._proximity_weight(None, cfg) == 0.0


def test_veto_overrides_tight_proximity():
    """Two different Pattons in one paragraph must NOT net positive."""
    cfg = load_dedup_config(None)
    _, veto = fdp._check_conflicting_middle(
        "George S. Patton", "George P. Patton", "patton", "patton", cfg
    )
    tight = fdp._proximity_weight(5, cfg)
    assert veto + tight < 0  # veto dominates even tightest proximity


def test_surname_rarity_nationality_aware():
    from src.dedup.config import load_dedup_config, surname_rarity_multiplier

    cfg = load_dedup_config(None)
    # Kowalski: rare among Americans (boost) vs very common among Poles (damp)
    assert surname_rarity_multiplier(cfg, "Kowalski", "USA") == 1.5
    assert surname_rarity_multiplier(cfg, "Kowalski", "POL") == 0.4
    # common Anglo surname -> damp even for US
    assert surname_rarity_multiplier(cfg, "Smith", "USA") == 0.4
    # no nationality OR surname not in table -> None (caller falls back to corpus)
    assert surname_rarity_multiplier(cfg, "Kowalski", "") is None
    assert surname_rarity_multiplier(cfg, "Vandervoort", "USA") is None


def test_rank_set_difference_is_promotion_aware():
    maj = {"biographical_profile": {"ranks": [{"rank": "Major"}]}}
    col = {"biographical_profile": {"ranks": [{"rank": "Colonel"}]}}
    span = {"biographical_profile": {"ranks": [{"rank": "Major"}, {"rank": "Colonel"}]}}
    assert fdp._ranks_differ(maj, col) is True  # disjoint -> different
    assert fdp._ranks_differ(maj, span) is False  # overlap -> not different
    assert fdp._ranks_differ(maj, {}) is False  # unknown -> not different


def test_rank_proximity_dampens_more_when_tight():
    """Major Smith vs Colonel Smith: tight proximity -> stronger negative than far."""
    from src.dedup.config import load_dedup_config

    cfg = load_dedup_config(None)
    # tight penalty should be more negative than near, which >= loose(0)
    assert cfg.rank_proximity["tight_penalty"] < cfg.rank_proximity["near_penalty"] <= 0
    assert cfg.rank_proximity["loose_penalty"] == 0.0


def test_surname_rarity_preferred_over_corpus_commonness():
    """When nationality + table entry exist, rarity multiplier applies (not the corpus
    commonness fallback)."""
    from src.dedup.config import load_dedup_config, surname_rarity_multiplier

    cfg = load_dedup_config(None)
    # A US Kowalski match gets boosted even if 'kowalski' were corpus-common.
    assert surname_rarity_multiplier(cfg, "Kowalski", "USA") > 1.0


def _person_units(units):
    return {"biographical_profile": {"units_served": units}}


def test_shared_unit_echelon_inverse_strength():
    from src.dedup.config import load_dedup_config

    cfg = load_dedup_config(None)

    def shared(ech):
        u = [{"unit": "X", "designation": "x", "echelon": ech}]
        return fdp._shared_unit_affiliation(_person_units(u), _person_units(u), cfg)[1]

    # smaller unit = stronger; regiment weak; corps/army = noise(0)
    assert (
        shared("squad") > shared("company") > shared("battalion") > shared("regiment")
    )
    assert shared("regiment") > shared("division")
    assert shared("corps") == 0.0
    # all are WEAK nudges (never decisive)
    assert shared("squad") < 1.0


def test_shared_unit_groupid_preferred_then_designation():
    from src.dedup.config import load_dedup_config

    cfg = load_dedup_config(None)
    # GroupID match
    a = _person_units([{"unit": "X", "GroupID": "01G", "echelon": "company"}])
    b = _person_units([{"unit": "Y", "GroupID": "01G", "echelon": "company"}])
    reasons, score = fdp._shared_unit_affiliation(a, b, cfg)
    assert score > 0 and "GroupID" in reasons[0]
    # designation fallback (no GroupID)
    c = _person_units(
        [{"unit": "502nd PIR", "designation": "502nd pir", "echelon": "regiment"}]
    )
    d = _person_units(
        [{"unit": "502 PIR", "designation": "502nd pir", "echelon": "regiment"}]
    )
    r2, s2 = fdp._shared_unit_affiliation(c, d, cfg)
    assert s2 > 0 and "designation" in r2[0]


def test_no_shared_unit_is_zero():
    from src.dedup.config import load_dedup_config

    cfg = load_dedup_config(None)
    a = _person_units([{"unit": "A", "designation": "a", "echelon": "company"}])
    b = _person_units([{"unit": "B", "designation": "b", "echelon": "company"}])
    assert fdp._shared_unit_affiliation(a, b, cfg) == ([], 0.0)
    # one has no units
    assert fdp._shared_unit_affiliation(a, {"biographical_profile": {}}, cfg) == (
        [],
        0.0,
    )


def test_shared_unit_disabled_via_config():
    from src.dedup.config import load_dedup_config

    cfg = load_dedup_config({"dedup": {"people": {"shared_unit": {"enabled": False}}}})
    u = [{"unit": "X", "designation": "x", "echelon": "squad"}]
    assert fdp._shared_unit_affiliation(_person_units(u), _person_units(u), cfg) == (
        [],
        0.0,
    )
