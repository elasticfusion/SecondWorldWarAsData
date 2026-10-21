"""Regression — §1.0c WARNING: locked_json.save() must run the central write-guard.

All read-modify-write MERGE paths (equipment/people/places/people_groups/dates/weather/enrich_write/
batch_parallel) persist via locked_json. Its save() previously called only _annotate_semantic_flags,
bypassing _validate_entity — so a merge that dropped a required field was written unguarded. save()
now validates (blocks a schema-invalid record, returns False, does NOT write) + stamps metadata, like
write_json_with_lock.
"""

import json
from pathlib import Path

from src.utils.file_lock import locked_json

_VALID_ULID = "01J0000000000000000000DATE"


def test_locked_json_save_blocks_schema_invalid(tmp_path):
    # a VERSIONLESS people record missing the required PersonID -> schema-invalid (treated as
    # malformed-not-old, so BLOCKED — mirrors test_versionless_invalid_is_blocked). The PK repair
    # mints an id but the record is still missing other required fields -> blocked.
    p = tmp_path / "output" / "people" / "bad.json"
    p.parent.mkdir(parents=True)
    with locked_json(p) as (data, save):
        ok = save({"rank": "General"})  # no PersonID, versionless
    assert ok is False  # blocked
    # locked_json opens the file (w+) at entry, so it may exist but EMPTY — the guard refused to
    # write the record content, which is what matters.
    content = p.read_text() if p.exists() else ""
    assert (
        "rank" not in content and "PersonID" not in content
    )  # record content NOT persisted


def test_locked_json_save_persists_valid(tmp_path):
    p = tmp_path / "output" / "dates" / "good.json"
    p.parent.mkdir(parents=True)
    with locked_json(p) as (data, save):
        ok = save({"DateID": _VALID_ULID, "date_start": "1944-12-16"})
    assert ok is True
    written = json.loads(p.read_text())
    assert written["DateID"] == _VALID_ULID
    assert "_schema_version" in written  # metadata stamped (like write_json_with_lock)


def test_locked_json_save_does_not_stamp_non_entity_file(tmp_path):
    """A non-entity bookkeeping file inside an entity dir (index.json) must round-trip UNCHANGED —
    _resolve_entity_type returns 'people' for it, so the stamp gate must also exclude non-entity
    files (regression for the stamping bug the round-1 review caught)."""
    p = tmp_path / "output" / "people" / "index.json"
    p.parent.mkdir(parents=True)
    payload = {"some": "index", "entries": [1, 2, 3]}
    with locked_json(p) as (data, save):
        ok = save(dict(payload))
    assert ok is True
    written = json.loads(p.read_text())
    assert written == payload  # no _schema_version / _last_updated injected
    assert "_schema_version" not in written
