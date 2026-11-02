"""Step 5c-i: deterministic Place/Date/Organization linking for maps, from the map's reference-
footprint sub-events (reuses the event_mentions[].Sub_eventID matching pattern). No Grok.
"""

import json
import ulid
from pathlib import Path

from src.extraction.map_common import link_map_entities, _resolve_entity_for_subevents


def _entity(d: Path, sub, id_field, id_val, name_field, name_val):
    d.mkdir(parents=True, exist_ok=True)
    (d / "e.json").write_text(
        json.dumps(
            {
                id_field: id_val,
                name_field: name_val,
                "event_mentions": [{"Sub_eventID": sub}],
            }
        )
    )
    (d / "index.json").write_text(json.dumps({"k": "e.json"}))


def test_links_place_date_org_from_subevent(tmp_path):
    pid, did, gid = str(ulid.new()), str(ulid.new()), str(ulid.new())
    _entity(tmp_path / "places", "S1", "PlaceID", pid, "current_name", "Utah Beach")
    _entity(tmp_path / "dates", "S1", "DateID", did, "date_start", "1944-06-06")
    _entity(
        tmp_path / "people_groups", "S1", "GroupID", gid, "group_name", "4th Inf Div"
    )
    rec = {
        "MapID": str(ulid.new()),
        "references": [{"paragraph_number": 1, "Sub_eventID": "S1"}],
    }
    link_map_entities(rec, tmp_path)
    assert rec["PlaceMentionID"] == pid and rec["place_name"] == "Utah Beach"
    assert rec["DateMentionID"] == did
    assert rec["PeopleGroupID"] == gid and rec["PeopleGroup_Name"] == "4th Inf Div"


def test_idempotent_does_not_overwrite_existing_link(tmp_path):
    pid = str(ulid.new())
    _entity(tmp_path / "places", "S1", "PlaceID", pid, "current_name", "New")
    rec = {
        "MapID": "m",
        "PlaceMentionID": "EXISTING",
        "references": [{"Sub_eventID": "S1"}],
    }
    link_map_entities(rec, tmp_path)
    assert rec["PlaceMentionID"] == "EXISTING"  # not overwritten


def test_no_subevent_no_link(tmp_path):
    _entity(tmp_path / "places", "S1", "PlaceID", str(ulid.new()), "current_name", "X")
    rec = {"MapID": "m", "references": [{"paragraph_number": 5}]}  # no Sub_eventID
    link_map_entities(rec, tmp_path)
    assert "PlaceMentionID" not in rec or rec.get("PlaceMentionID") is None


def test_missing_entity_dir_graceful(tmp_path):
    assert _resolve_entity_for_subevents(
        tmp_path / "nope", {"S1"}, "PlaceID", "current_name"
    ) == (None, None)
