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
    st.write_json("people/bad.json", {"name": "No ID"})  # missing PersonID
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
    # No _schema_version + missing PersonID = malformed, NOT "legitimately old" -> block.
    assert _validate_entity(Path("/x/people/a.json"), {"name": "X"}) is False


def test_current_version_invalid_is_blocked():
    cur = entity_version("people")
    assert (
        _validate_entity(
            Path("/x/people/b.json"), {"name": "X", "_schema_version": cur}
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


def test_old_versioned_but_pk_less_is_blocked():
    """Reviewers' hole: a record with a plausible OLD _schema_version but MISSING its primary
    key must still be BLOCKED — the PK was never a version-added field, so a PK-less record is
    corrupt regardless of version. (Was previously waved through as needs_upgrade.)"""
    # people fragment: declares old version, has event_mentions, but NO PersonID + no name.
    frag = {"_schema_version": "2.0", "rank": "General", "event_mentions": []}
    assert _validate_entity(Path("/x/people/krueger.json"), frag) is False
    # groups fragment, old version, no GroupID -> blocked
    gfrag = {"_schema_version": "2.0", "nationality": "USA"}
    assert _validate_entity(Path("/x/people_groups/div.json"), gfrag) is False


def test_old_versioned_with_pk_missing_nonpk_field_is_allowed():
    """Contrast: old-versioned record that HAS its PK but lacks a genuinely-newer required
    field (dates.date_start) is still allowed-with-warning (not lost)."""
    rec = {"DateID": VALID_ULID, "_schema_version": "2.0"}  # has PK, missing date_start
    assert _validate_entity(Path("/x/dates/old.json"), rec) is True
