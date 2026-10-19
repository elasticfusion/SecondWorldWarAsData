"""MED fix: person records must have name + PersonID (legacy records lacked both)."""

import json
import re
import tempfile
import pathlib

from src.extraction.people import backfill_people_identity


def test_backfill_repairs_missing_name_and_personid(tmp_path):
    # legacy broken record: no name, no PersonID; name recoverable from filename stem
    (tmp_path / "omar n bradley.json").write_text(
        json.dumps({"rank": "General", "nationality": "USA", "event_mentions": []})
    )
    # healthy record untouched
    (tmp_path / "patton.json").write_text(
        json.dumps(
            {"name": "George S. Patton", "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW"}
        )
    )
    scanned, fixed = backfill_people_identity(tmp_path)
    assert scanned == 2 and fixed == 1
    b = json.loads((tmp_path / "omar n bradley.json").read_text())
    assert b["name"] == "omar n bradley"
    assert re.match(r"^[0-9A-HJKMNP-TV-Z]{26}$", b["PersonID"])
    p = json.loads((tmp_path / "patton.json").read_text())
    assert p["PersonID"] == "01HX7YZABCDEFGHJKMNPQRSTVW"  # unchanged
    # index/hidden files skipped
    assert scanned == 2
