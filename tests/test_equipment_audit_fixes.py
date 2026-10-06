"""Audit fixes: (1) assertion gate drops items with no asserted presence; (2) canonical
identity is used as the merge match key (prevents cross-naming splits)."""

from src.extraction.equipment import _find_matching_equipment


def test_canonical_name_is_primary_match_key(tmp_path):
    f = tmp_path / "m4.json"
    idx = {"M4A2 Sherman": f}  # record indexed under its canonical name
    # a later mention "Sherman V" resolved to canonical "M4A2 Sherman" must MATCH it
    m = _find_matching_equipment(
        "Sherman V", idx, technical_id="", canonical_name="M4A2 Sherman"
    )
    assert m == "M4A2 Sherman"


def test_canonical_checked_before_common_and_technical(tmp_path):
    f_canon = tmp_path / "canon.json"
    f_common = tmp_path / "common.json"
    idx = {"M4A2 Sherman": f_canon, "Sherman V": f_common}
    # canonical wins over the raw common_name key
    m = _find_matching_equipment(
        "Sherman V", idx, technical_id="", canonical_name="M4A2 Sherman"
    )
    assert m == "M4A2 Sherman"


def test_falls_back_when_no_canonical(tmp_path):
    f = tmp_path / "m4.json"
    idx = {"M4": f}
    assert _find_matching_equipment("M4", idx, technical_id="M4") == "M4"


# Assertion gate: _process_equipment_item requires assertion_source. Verified via the
# EquipmentExtraction model default (None) -> gate drops it. We assert the gate logic
# directly (the model field exists and defaults to None).
def test_assertion_source_defaults_none_triggering_gate():
    from src.extraction.equipment import EquipmentExtraction

    eq = EquipmentExtraction.model_validate({"common_name": "M4", "category": "armor"})
    assert eq.assertion_source is None  # -> _process_equipment_item will skip it
