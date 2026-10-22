"""Regression: a hallucinated/impossible `_schema_version` must be VALIDATED-AND-FIXED (treated
like a bad ULID), not allowed to bypass schema validation.

A record written with a `_schema_version` NEWER than the entity's target previously hit the
version-aware guard's "future -> allow AS-IS" branch and was persisted WITHOUT schema validation
(a validation bypass an LLM could trigger by hallucinating a version). `_repair_schema_version`
now resets an impossible future version to the target before the status check, so the record is
really validated; a legitimately-old version (<= target) is left for the normal upgrade path.
"""

import json
import logging
import tempfile
from pathlib import Path

from src.schemas import entity_version
from src.utils.file_lock import write_json_with_lock

ULID = "01HX7YZABCDEFGHJKMNPQRSTVW"
ULID2 = "01HX7YZABCDEFGHJKMNPQRSTVX"


def _people_dir():
    d = Path(tempfile.mkdtemp()) / "people"
    d.mkdir()
    return d


def test_future_version_valid_record_repaired_and_written(caplog):
    d = _people_dir()
    rec = {
        "PersonID": ULID,
        "name": "Valid Person",
        "_schema_version": "99.0",  # hallucinated future version
        "biographical_profile": {},
    }
    with caplog.at_level(logging.WARNING, logger="src.utils.file_lock"):
        ok = write_json_with_lock(d / "a.json", rec, entity="people")
    assert ok is True
    written = json.loads((d / "a.json").read_text())
    # Repaired down to the real target (not left as 99.0).
    assert written["_schema_version"] == entity_version("people")
    assert any(
        "Auto-repaired" in r.message and "_schema_version" in r.message
        for r in caplog.records
    )


def test_future_version_INVALID_record_is_blocked_not_bypassed():
    """The core fix: an invalid record carrying a future version must be BLOCKED (schema-validated
    after the version repair), NOT silently allowed AS-IS."""
    d = _people_dir()
    rec = {
        "PersonID": ULID2,
        # 'name' is required and MISSING -> schema-invalid
        "_schema_version": "99.0",
        "biographical_profile": {},
    }
    ok = write_json_with_lock(d / "b.json", rec, entity="people")
    assert (
        ok is False
    ), "invalid record with a future version was NOT blocked (validation bypass)"
    assert not (d / "b.json").exists()


def test_absent_and_old_versions_untouched():
    d = _people_dir()
    # Absent version -> stamped to target, valid record writes fine.
    ok = write_json_with_lock(
        d / "c.json", {"PersonID": ULID, "name": "P"}, entity="people"
    )
    assert ok is True
    # A legitimately-OLD version (<= target) is NOT treated as impossible — valid record writes.
    ok2 = write_json_with_lock(
        d / "d.json",
        {"PersonID": ULID2, "name": "Q", "_schema_version": "2.0"},
        entity="people",
    )
    assert ok2 is True


def test_garbage_and_equal_versions_not_repaired():
    """Edge guard: a MALFORMED version ("garbage" -> version tuple (0,) < target) is NOT treated
    as impossible (left for normal validation, not reset to target), and a version EQUAL to target
    is untouched. Locks the (0,)/equality boundaries against future _version_tuple refactors.
    """
    from src.schemas import entity_version

    d = _people_dir()
    tgt = entity_version("people")
    # Equal-to-target: valid record writes, version stays == target (unchanged).
    ok = write_json_with_lock(
        d / "eq.json",
        {"PersonID": ULID, "name": "P", "_schema_version": tgt},
        entity="people",
    )
    assert ok is True
    assert json.loads((d / "eq.json").read_text())["_schema_version"] == tgt
    # Garbage version on a VALID record: not mis-flagged as "future"; record is validated + written
    # (version gets stamped to target by inject_metadata, as for any accepted write).
    ok2 = write_json_with_lock(
        d / "g.json",
        {"PersonID": ULID2, "name": "Q", "_schema_version": "garbage"},
        entity="people",
    )
    assert ok2 is True
