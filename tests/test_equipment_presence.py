"""Absent/shortage equipment is a valid fact: it must validate, pass the assertion gate even
without assertion_source, and PERSIST presence/absence_reason to the record."""

from src.extraction.equipment import EquipmentExtraction, _build_equipment_data


def test_absent_mention_validates_and_defaults():
    e = EquipmentExtraction.model_validate(
        {
            "name": "aircraft",
            "common_name": "aircraft",
            "category": "aircraft",
            "presence": "absent",
            "absence_reason": "battlefield",
            "original_text": "not a single German plane",
        }
    )
    assert e.presence == "absent" and e.absence_reason == "battlefield"


def test_present_is_default():
    e = EquipmentExtraction.model_validate(
        {"name": "M4", "common_name": "M4", "category": "armor"}
    )
    assert e.presence == "present" and e.absence_reason is None


def test_presence_persists_to_record():
    e = EquipmentExtraction.model_validate(
        {
            "name": "artillery",
            "common_name": "artillery",
            "category": "artillery",
            "presence": "absent",
            "absence_reason": "logistics",
            "original_text": "two AA gun battalions could not come in until the following day",
        }
    )
    d = _build_equipment_data(e)
    assert d["presence"] == "absent" and d["absence_reason"] == "logistics"


def test_present_record_has_no_absence_reason_key():
    e = EquipmentExtraction.model_validate(
        {"name": "M4", "common_name": "M4", "category": "armor"}
    )
    d = _build_equipment_data(e)
    assert d["presence"] == "present" and "absence_reason" not in d
