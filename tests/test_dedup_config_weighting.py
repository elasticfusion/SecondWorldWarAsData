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
