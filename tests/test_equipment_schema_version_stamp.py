"""PR5 (a): a newly-created equipment record must be stamped with the EQUIPMENT schema version
(via entity='equipment'), not the MIN version — otherwise it's needs_migration forever.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.file_lock import write_json_with_lock
from src.schemas import ENTITY_SCHEMA_VERSIONS


def test_new_equipment_stamped_with_equipment_version(tmp_path: Path):
    eq_dir = tmp_path / "equipment"
    eq_dir.mkdir()
    rec = {"EquipmentID": "01HTEST0000000000000000EQ", "common_name": "Panzer IV"}
    f = eq_dir / "equipment_01HTEST00000.json"
    assert write_json_with_lock(f, rec, entity="equipment")
    stamped = json.loads(f.read_text())["_schema_version"]
    assert stamped == ENTITY_SCHEMA_VERSIONS["equipment"], (
        f"new equipment stamped {stamped}, expected the equipment version "
        f"{ENTITY_SCHEMA_VERSIONS['equipment']} (missing entity='equipment' -> MIN -> needs_migration)"
    )
