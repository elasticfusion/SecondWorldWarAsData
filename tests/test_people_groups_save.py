"""_save_group must write a GroupID into every record (the primary key cross-references
resolve to); schema accepts member_countries/members (drift fixed)."""

import json
import jsonschema
from pathlib import Path

from src.extraction.people_groups import _save_group
from src.schemas.groups_output import GROUPS_OUTPUT_SCHEMA as S


def _saved(tmp_path, group):
    gd = tmp_path / "people_groups"
    gd.mkdir()
    _save_group(gd, gd / "index.json", group, "BookX", "AuthorY", "SeriesZ")
    files = [f for f in gd.glob("*.json") if f.name != "index.json"]
    return json.loads(files[0].read_text())


def test_groupid_injected_when_missing(tmp_path):
    rec = _saved(
        tmp_path,
        {
            "group_name": "3rd Panzer Grenadier Division",
            "nationality": "Germany",
            "event_mentions": [],
        },
    )
    assert rec.get("GroupID"), "record written without a GroupID (the bug)"
    jsonschema.validate(rec, S)


def test_empty_string_groupid_replaced(tmp_path):
    rec = _saved(
        tmp_path,
        {
            "group_name": "Army Group B",
            "GroupID": "",
            "nationality": "Germany",
            "event_mentions": [],
        },
    )
    assert rec["GroupID"] and rec["GroupID"] != ""
    jsonschema.validate(rec, S)


def test_provided_groupid_preserved(tmp_path):
    gid = "01HX7YZABCDEFGHJKMNPQRSTVW"
    rec = _saved(
        tmp_path, {"group_name": "7th Armored", "GroupID": gid, "event_mentions": []}
    )
    assert rec["GroupID"] == gid


def test_schema_accepts_member_countries_and_members():
    rec = {
        "GroupID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "member_countries": ["USA", "UK"],
        "members": [{"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "x"}],
    }
    jsonschema.validate(rec, S)
