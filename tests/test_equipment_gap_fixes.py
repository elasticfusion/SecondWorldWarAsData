"""Fixes: #2 dedup uses record canonical_name; #3 supporting-equipment resolves
nickname/fuzzy -> canonical EquipmentID (not name-exact)."""

import json
from pathlib import Path

from scripts.find_duplicate_equipment import _all_names
from src.extraction.equipment import (
    _resolve_support_equipment_id,
    SupportingUnitInput,
    _link_supporting_units,
)


def test_dedup_all_names_includes_canonical():
    equip = {"common_name": "Sherman V", "canonical_name": "M4A2 Sherman"}
    names = [n.lower() for n in _all_names(equip)]
    assert "m4a2 sherman" in names  # canonical identity now participates in detection


def test_support_equipment_exact(tmp_path):
    f = tmp_path / "p47.json"
    f.write_text(
        json.dumps(
            {
                "EquipmentID": "01P47000000000000000000000",
                "common_name": "P-47 Thunderbolt",
            }
        )
    )
    idx = {"P-47 Thunderbolt": f}
    assert (
        _resolve_support_equipment_id("P-47 Thunderbolt", idx)
        == "01P47000000000000000000000"
    )


def test_support_equipment_nickname_via_alias(tmp_path):
    # 'thunderbolt' is a curated alias -> 'p-47 thunderbolt'
    f = tmp_path / "p47.json"
    f.write_text(
        json.dumps(
            {
                "EquipmentID": "01P47000000000000000000000",
                "common_name": "P-47 Thunderbolt",
            }
        )
    )
    idx = {"P-47 Thunderbolt": f}
    assert (
        _resolve_support_equipment_id("Thunderbolt", idx)
        == "01P47000000000000000000000"
    )


def test_support_equipment_fuzzy(tmp_path):
    f = tmp_path / "sherman.json"
    f.write_text(
        json.dumps(
            {"EquipmentID": "01SH0000000000000000000000", "common_name": "M4 Sherman"}
        )
    )
    idx = {"M4 Sherman": f}
    # slight variant resolves via fuzzy (>=0.80)
    assert (
        _resolve_support_equipment_id("M4 Sherman tank", idx)
        == "01SH0000000000000000000000"
    )


def test_support_equipment_none_when_absent(tmp_path):
    assert _resolve_support_equipment_id("Nonexistent", {}) is None
