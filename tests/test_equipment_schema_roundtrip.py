"""Regression lock: a record built by _build_equipment_data MUST validate against the equipment
OUTPUT schema. #652 added presence/absence_reason to the model+builder but NOT the schema, so
additionalProperties:False rejected 100% of equipment (18/18) on the 2026-10-31 measurement run.
"""

import jsonschema
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA
from src.extraction.equipment import EquipmentExtraction, _build_equipment_data


def _valid(d):
    d = dict(d)
    d.setdefault("EquipmentID", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    jsonschema.validate(d, EQUIPMENT_OUTPUT_SCHEMA)
    return True


def test_present_equipment_record_validates():
    e = EquipmentExtraction.model_validate(
        {
            "name": "LST",
            "common_name": "LST",
            "category": "naval",
            "assertion_source": "narrative",
        }
    )
    assert _valid(_build_equipment_data(e))


def test_absent_equipment_record_validates():
    e = EquipmentExtraction.model_validate(
        {
            "name": "artillery",
            "common_name": "artillery",
            "category": "artillery",
            "presence": "absent",
            "absence_reason": "battlefield",
            "original_text": "no supporting artillery",
        }
    )
    d = _build_equipment_data(e)
    assert d["presence"] == "absent" and d["absence_reason"] == "battlefield"
    assert _valid(d)


def test_schema_has_presence_and_absence_reason():
    props = EQUIPMENT_OUTPUT_SCHEMA["properties"]
    assert "presence" in props and "absence_reason" in props
