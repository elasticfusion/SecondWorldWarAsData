"""Guard: military UNITS/FORMATIONS captured as equipment by broad are routed OUT of equipment.

The chapter-8 validation run showed broad over-captured formations (10th SS Panzer Division, 326th
Division, II SS Panzer Corps...) as 'equipment' (~75% of a section's records). The refine pass flags
such leaks with NOT_EQUIPMENT_FORMATION at the start of context; the code gate routes them out (loud
OBSERV equipment.formation_rejected), while genuine equipment is kept.
"""

from src.extraction.equipment import (
    EquipmentExtraction,
    _equipment_is_formation_leak,
)


def _eq(**kw):
    base = {
        "common_name": "x",
        "category": "other",
        "presence": "present",
        "assertion_source": "narrative",
    }
    base.update(kw)
    return EquipmentExtraction.model_validate(base)


def test_formation_leak_is_routed_out():
    leak = _eq(
        common_name="21st Panzer Division",
        context="NOT_EQUIPMENT_FORMATION a body of troops",
    )
    assert _equipment_is_formation_leak(leak) is True


def test_genuine_equipment_is_kept():
    real = _eq(common_name="Panzer IV", category="armor", context="German medium tank")
    assert _equipment_is_formation_leak(real) is False


def test_empty_context_is_kept():
    assert _equipment_is_formation_leak(_eq(common_name="LST", context=None)) is False
