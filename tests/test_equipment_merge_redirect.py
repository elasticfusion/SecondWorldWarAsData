"""Cross-feature invariant: when equipment records merge, references to the merged-away
EquipmentID are redirected to the survivor (no dangling refs) — same as people/groups.
"""

import json
from pathlib import Path

from src.dedup.merge import update_event_refs


def test_equipment_ref_redirect_in_event_files(tmp_path):
    out = tmp_path
    (out / "ch1-event.json").write_text(
        json.dumps(
            {
                "Event": {
                    "EventID": "01EV",
                    "Sub-events": [
                        {
                            "Sub-eventID": "01SE",
                            "equipment": [
                                "01LOSER0000000000000000000",
                                "01OTHER000000000000000000",
                            ],
                        }
                    ],
                }
            }
        )
    )
    update_event_refs(
        out, "01LOSER0000000000000000000", "01SURVIVOR000000000000000", "equipment"
    )
    d = json.loads((out / "ch1-event.json").read_text())
    refs = d["Event"]["Sub-events"][0]["equipment"]
    assert "01LOSER0000000000000000000" not in refs
    assert "01SURVIVOR000000000000000" in refs  # redirected to survivor
    assert "01OTHER000000000000000000" in refs  # untouched


def test_equipment_ref_redirect_in_entity_files(tmp_path):
    out = tmp_path
    loser = "01HX7YZABCDEFGHJKMNPQRSTVW"
    survivor = "01HX7YZABCDEFGHJKMNPQRSTVX"
    (out / "logistics").mkdir()
    (out / "logistics" / "l1.json").write_text(
        json.dumps(
            {
                "LogisticsID": "01HX7YZABCDEFGHJKMNPQRSTVY",
                "logistics_type": "resupply",
                "impacted_equipment": [loser],
            }
        )
    )
    update_event_refs(out, loser, survivor, "equipment")
    d = json.loads((out / "logistics" / "l1.json").read_text())
    assert d["impacted_equipment"] == [survivor]
