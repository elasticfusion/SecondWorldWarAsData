"""Cross-entity merge redirect: when a place merges, a PlaceID held INSIDE an equipment
record (mention) is rebased to the survivor — not left dangling. (The redirect scan now
covers equipment/people/people_groups, not just logistics/casualties/weather.)"""

import json
from src.dedup.merge import update_event_refs

OLD = "01HX7YZABCDEFGHJKMNPQRSTVW"
NEW = "01HX7YZABCDEFGHJKMNPQRSTVX"


def test_place_merge_rebases_equipment_mention_placeid(tmp_path):
    (tmp_path / "equipment").mkdir()
    (tmp_path / "equipment" / "m4.json").write_text(
        json.dumps(
            {
                "EquipmentID": "01HX7YZABCDEFGHJKMNPQRSTVY",
                "common_name": "M4 Sherman",
                "event_mentions": [
                    {"MentionID": "01HX7YZABCDEFGHJKMNPQRSTVZ", "PlaceID": OLD}
                ],
            }
        )
    )
    update_event_refs(tmp_path, OLD, NEW, "places")
    rec = json.loads((tmp_path / "equipment" / "m4.json").read_text())
    assert rec["event_mentions"][0]["PlaceID"] == NEW  # rebased, not dangling


def test_person_merge_rebases_equipment_using_person(tmp_path):
    (tmp_path / "equipment").mkdir()
    (tmp_path / "equipment" / "m4.json").write_text(
        json.dumps(
            {
                "EquipmentID": "01HX7YZABCDEFGHJKMNPQRSTVY",
                "event_mentions": [
                    {
                        "MentionID": "01HX7YZABCDEFGHJKMNPQRSTVZ",
                        "using_person": {"PersonID": OLD},
                    }
                ],
            }
        )
    )
    update_event_refs(tmp_path, OLD, NEW, "people")
    rec = json.loads((tmp_path / "equipment" / "m4.json").read_text())
    assert rec["event_mentions"][0]["using_person"]["PersonID"] == NEW
