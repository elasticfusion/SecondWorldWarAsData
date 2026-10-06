"""environmental_performance: condition-linked equipment performance (M4 in sub-zero),
narrative-sourced (original_text mandatory), merge-accumulated, schema-valid."""

import jsonschema
from src.extraction.equipment import (
    EnvironmentalPerformanceInput,
    _link_environmental_performance,
    _merge_environmental_performance,
)
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


def test_requires_original_text():
    out = _link_environmental_performance(
        [
            EnvironmentalPerformanceInput(
                condition="sub-zero temperatures", effect="performed badly"
            ),  # no original_text -> dropped
            EnvironmentalPerformanceInput(
                condition="sub-zero temperatures",
                effect="performed badly",
                original_text="the M4 performed badly in sub-zero temperatures",
            ),
        ]
    )
    assert len(out) == 1
    assert out[0]["condition"] == "sub-zero temperatures"
    assert out[0]["original_text"]


def test_merge_accumulates_dedup():
    existing = {
        "environmental_performance": [{"condition": "snow", "original_text": "t1"}]
    }
    incoming = {
        "environmental_performance": [
            {"condition": "snow", "original_text": "t1"},  # dup
            {"condition": "mud", "original_text": "t2"},
        ]
    }  # new
    _merge_environmental_performance(existing, incoming)
    conds = {
        (e["condition"], e["original_text"])
        for e in existing["environmental_performance"]
    }
    assert conds == {("snow", "t1"), ("mud", "t2")}


def test_schema_validates():
    rec = {
        "EquipmentID": UL,
        "common_name": "M4 Sherman",
        "environmental_performance": [
            {
                "condition": "sub-zero temperatures",
                "effect": "performed badly",
                "original_text": "the M4 performed badly in sub-zero temperatures",
            }
        ],
    }
    jsonschema.validate(rec, S)
