"""Deterministic operating_country backstop (the LLM is inconsistent on borderline spans).

Chapter-8 run: an 'assault gun' owned by German OB WEST divisions came back operating_country=null
despite the refine rule, because the model is non-deterministic on spans about formations
RELINQUISHING gear. The code backstop fills operating_country ONLY on an unambiguous single-side
cue (side-specific weapon name, or formations/commands of exactly one side); cue-less/mixed stay
null (no over-reach); a model-provided value is never overridden.
"""

from src.extraction.equipment import _backfill_operating_country, _detect_equipment_side


def test_axis_weapon_name_fills_deu():
    rec = {"common_name": "Nebelwerfer", "original_text": "rocket launchers fired"}
    _backfill_operating_country(rec, "")
    assert rec["operating_country"] == "DEU"


def test_axis_only_formation_fills_deu():
    rec = {
        "common_name": "assault gun",
        "original_text": "four divisions under OB WEST gave up their assault guns",
    }
    _backfill_operating_country(rec, "German Wehrmacht sector")
    assert rec["operating_country"] == "DEU"


def test_allied_only_formation_fills_usa():
    rec = {
        "common_name": "half-track",
        "original_text": "the U.S. First Army half-tracks advanced",
    }
    _backfill_operating_country(rec, "American VII Corps")
    assert rec["operating_country"] == "USA"


def test_mixed_sides_stays_null():
    rec = {
        "common_name": "tank",
        "original_text": "U.S. First Army engaged the SS Panzer division",
    }
    _backfill_operating_country(rec, "")
    assert rec.get("operating_country") is None


def test_cueless_stays_null():
    rec = {"common_name": "rifle", "original_text": "a rifle lay in the mud"}
    _backfill_operating_country(rec, "")
    assert rec.get("operating_country") is None


def test_model_value_not_overridden():
    rec = {
        "common_name": "Nebelwerfer",
        "operating_country": "GBR",
        "original_text": "x",
    }  # (implausible, but must not be clobbered)
    _backfill_operating_country(rec, "")
    assert rec["operating_country"] == "GBR"


def test_detect_side_helper():
    assert _detect_equipment_side({"common_name": "StuG"}, "")[0] == "DEU"
    assert _detect_equipment_side({"common_name": "truck"}, "")[0] is None
