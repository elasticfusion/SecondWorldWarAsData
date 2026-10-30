"""Equipment: the prompt emits `name` ("M4 Sherman") but EquipmentExtraction requires `common_name`
— so every item failed validation and was dropped (0 equipment on a D-Day chapter; groups-class bug,
XChannel-8 2026-10-29). The parse path must coerce name->common_name before validation.
"""

from src.extraction.equipment import EquipmentExtraction


def _coerce(eq_data: dict) -> dict:
    # mirrors the coercion in _process_equipment_item
    if (
        isinstance(eq_data, dict)
        and not eq_data.get("common_name")
        and eq_data.get("name")
    ):
        eq_data["common_name"] = eq_data["name"]
    return eq_data


def test_equipment_name_coerced_to_common_name_validates():
    # a realistic prompt-shaped object using `name` (not common_name)
    eq_data = {"name": "M4 Sherman", "category": "armor"}
    eq = EquipmentExtraction.model_validate(_coerce(dict(eq_data)))
    assert eq.common_name == "M4 Sherman"


def test_equipment_explicit_common_name_wins():
    eq_data = {"name": "Sherman", "common_name": "M4 Sherman", "category": "armor"}
    assert _coerce(dict(eq_data))["common_name"] == "M4 Sherman"


def test_process_equipment_item_coercion_present_in_source():
    import pathlib

    src = pathlib.Path("src/extraction/equipment.py").read_text(encoding="utf-8")
    flat = " ".join(src.split())
    assert 'eq_data["common_name"] = eq_data["name"]' in flat
