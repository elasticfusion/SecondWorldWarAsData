"""Schema contract: code targets a version; older records upgrade case-by-case; a record
written by a FUTURE (newer) schema errors gracefully rather than being silently processed.
"""

import logging

import pytest

from src.schemas.schema_contract import (
    FutureSchemaError,
    read_record,
    read_for_update,
    register_upgrade,
)


def test_ok_when_version_matches():
    rec, st = read_record({"_schema_version": "2.24", "x": 1}, "2.24")
    assert st == "ok" and rec["x"] == 1


def test_future_record_raises_by_default():
    # code targeting 2.24 reading a 2.25 record must NOT silently proceed
    with pytest.raises(FutureSchemaError):
        read_record({"_schema_version": "2.25"}, "2.24")


def test_future_record_flagged_when_non_strict():
    rec, st = read_record({"_schema_version": "3.0"}, "2.24", strict=False)
    assert st == "future" and rec == {"_schema_version": "3.0"}


def test_needs_upgrade_when_older_and_no_upgrader():
    rec, st = read_record({"_schema_version": "2.20"}, "2.24")
    assert st == "needs_upgrade" and rec == {"_schema_version": "2.20"}


def test_registered_upgrade_runs():
    @register_upgrade("2.21", "2.24")
    def _up(r):
        r["added"] = True
        return r

    rec, st = read_record({"_schema_version": "2.21"}, "2.24")
    assert st == "upgraded" and rec["added"] is True


def test_missing_version_treated_as_oldest():
    _, st = read_record({"no": "version"}, "2.24")
    assert st == "needs_upgrade"


def test_read_for_update_skips_future(caplog):
    log = logging.getLogger("t")
    with caplog.at_level(logging.WARNING):
        rec, skip = read_for_update({"_schema_version": "2.99"}, "2.24", log, "GroupX")
    assert skip is True
    assert any(
        "NEWER" in r.message or "future" in r.message.lower() for r in caplog.records
    )


def test_read_for_update_proceeds_on_ok_and_older():
    log = logging.getLogger("t")
    _, skip = read_for_update({"_schema_version": "2.24"}, "2.24", log)
    assert skip is False
    _, skip2 = read_for_update({"_schema_version": "2.10"}, "2.24", log)
    assert skip2 is False  # older -> best-effort, not skipped
