"""Tests for the centralized, version-aware write guard (new behaviors).

Covers the pieces added when validation was centralized across ALL write paths:
  - Storage.write_json (Local) now blocks schema-invalid entity records.
  - Events resolve by '-event.json' filename suffix (they live under output/content/<book>/).
  - Version-aware: version-LESS invalid -> blocked; genuinely-older (declares old version,
    no upgrader) missing a now-required field -> allowed (not lost); future -> allowed as-is.
"""

import tempfile
from pathlib import Path

import pytest

from src.schemas import entity_version
from src.utils.file_lock import _validate_entity, write_json_with_lock
from src.utils.storage import LocalStorage

VALID_ULID = "01HX7YZABCDEFGHJKMNPQRSTVW"


@pytest.fixture
def base():
    return Path(tempfile.mkdtemp())


# ---- Storage.write_json guard (the UI-merge / lambda path) ----


def test_storage_write_json_blocks_invalid(base):
    st = LocalStorage(base)
    # missing 'name' (people needs PersonID + name). PK auto-heals, but name is unrecoverable -> block.
    st.write_json("people/bad.json", {"rank": "Gen"})
    assert not (base / "people/bad.json").exists()


def test_storage_write_json_allows_valid(base):
    st = LocalStorage(base)
    st.write_json("people/good.json", {"PersonID": VALID_ULID, "name": "Good"})
    assert (base / "people/good.json").exists()


def test_storage_write_json_passes_non_entity(base):
    st = LocalStorage(base)
    st.write_json(
        "people/index.json", {"anything": 1}
    )  # index = non-entity -> passthrough
    assert (base / "people/index.json").exists()


# ---- events resolved by filename suffix ----


def test_events_resolved_by_suffix_and_guarded():
    # output/content/<Book>/<chapter>-event.json -> entity 'events' (not the book dir name)
    p = Path("output/content/TheArdennes/chapter1a-event.json")
    assert _validate_entity(p, {"foo": "bar"}) is False  # invalid event blocked
    valid_event = {
        "Event": {
            "EventID": VALID_ULID,
            "Event_Name": "Operation X",
            "Sub-events": [{"Sub-eventID": "01HX7YZABCDEFGHJKMNPQRSTVX"}],
        }
    }
    assert _validate_entity(p, valid_event) is True  # valid event allowed


# ---- version-aware branches ----


def test_versionless_invalid_is_blocked():
    # Missing PK is now AUTO-HEALED (ULID minted). But a record ALSO missing another required
    # field still blocks — here people requires both PersonID AND name; name is absent -> block.
    assert _validate_entity(Path("/x/people/a.json"), {"rank": "Gen"}) is False


def test_current_version_invalid_is_blocked():
    cur = entity_version("people")
    # PK auto-heals; blocked here because 'name' (also required) is missing.
    assert (
        _validate_entity(
            Path("/x/people/b.json"), {"rank": "Gen", "_schema_version": cur}
        )
        is False
    )


def test_genuinely_old_missing_now_required_is_allowed():
    # A dates record declaring an OLD version, missing the now-required date_start, with no
    # registered upgrader -> allowed-with-warning (data not lost), NOT blocked.
    rec = {"DateID": VALID_ULID, "_schema_version": "2.0"}
    assert _validate_entity(Path("/x/dates/old.json"), rec) is True


def test_versionless_missing_now_required_is_blocked():
    # Same shape but NO declared version -> malformed -> blocked.
    rec = {"DateID": VALID_ULID}
    assert _validate_entity(Path("/x/dates/bad.json"), rec) is False


def test_future_record_allowed_as_is():
    # Record NEWER than current code's target -> allowed as-is (never block/mutate).
    cur = entity_version("people")
    future = ".".join([str(int(cur.split(".")[0]) + 9)] + cur.split(".")[1:])
    rec = {
        "name": "X",
        "_schema_version": future,
    }  # even without PersonID -> allowed as-is
    assert _validate_entity(Path("/x/people/f.json"), rec) is True


def test_validate_before_stamp_old_record_not_lost(base):
    # Through write_json_with_lock: a genuinely-old dates record missing date_start must be
    # WRITTEN (validate sees true old version before inject_metadata stamps current).
    d = base / "dates"
    d.mkdir()
    write_json_with_lock(
        d / "old.json", {"DateID": VALID_ULID, "_schema_version": "2.0"}, entity="dates"
    )
    assert (d / "old.json").exists()


def test_pk_less_but_otherwise_valid_is_healed():
    """Policy change: a record missing ONLY its primary key is AUTO-HEALED (ULID minted), not
    blocked — the PK is self-identity, no reanalysis needed. (This heals legacy fragments like
    the 25 places blocks.) Records missing OTHER required fields still block (covered elsewhere).
    """
    # people needs PersonID + name; supply name, omit PersonID -> healed + allowed.
    rec = {"_schema_version": "2.0", "name": "Walter Krueger", "rank": "General"}
    assert _validate_entity(Path("/x/people/krueger.json"), rec) is True
    import re

    assert re.match(r"^[0-9A-HJKMNP-TV-Z]{26}$", rec["PersonID"])  # minted in place


def test_old_versioned_with_pk_missing_nonpk_field_is_allowed():
    """Contrast: old-versioned record that HAS its PK but lacks a genuinely-newer required
    field (dates.date_start) is still allowed-with-warning (not lost)."""
    rec = {"DateID": VALID_ULID, "_schema_version": "2.0"}  # has PK, missing date_start
    assert _validate_entity(Path("/x/dates/old.json"), rec) is True


def test_pk_auto_repair_mints_missing_ulid(base):
    """A record missing its primary-key ULID is AUTO-HEALED (ULID minted) + written, across
    features — the PK is self-identity, no reanalysis needed. (Would have healed the 25 places
    blocks.)"""
    import re

    ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
    # places fragment missing PlaceID (realistic: valid nested refs)
    d = base / "places"
    d.mkdir()
    rec = {
        "event_mentions": [
            {"EventID": VALID_ULID, "MentionID": VALID_ULID, "Sub_eventID": VALID_ULID}
        ]
    }
    write_json_with_lock(d / "crozon.json", rec, entity="places")
    assert (d / "crozon.json").exists()
    import json

    got = json.loads((d / "crozon.json").read_text())
    assert ULID.match(got["PlaceID"])  # minted
    assert got["event_mentions"][0]["EventID"] == VALID_ULID  # reference preserved


def test_pk_auto_repair_fixes_malformed(base):
    import json
    import re

    d = base / "people"
    d.mkdir()
    write_json_with_lock(
        d / "x.json", {"PersonID": "BADID", "name": "N"}, entity="people"
    )
    pid = json.loads((d / "x.json").read_text())["PersonID"]
    assert re.match(r"^[0-9A-HJKMNP-TV-Z]{26}$", pid) and pid != "BADID"


def test_pk_repair_does_not_mask_other_missing_required(base):
    # dates needs DateID + date_start; PK repair fixes DateID but date_start still missing -> BLOCK
    d = base / "dates"
    d.mkdir()
    write_json_with_lock(d / "d.json", {}, entity="dates")
    assert not (d / "d.json").exists()


def test_pk_repair_never_regenerates_reference_ids(base):
    # A bad nested REFERENCE id (EventID) must still BLOCK — never silently regenerated.
    d = base / "places"
    d.mkdir()
    rec = {"event_mentions": [{"EventID": "NOTAULID"}]}
    write_json_with_lock(d / "bad.json", rec, entity="places")
    assert not (d / "bad.json").exists()
