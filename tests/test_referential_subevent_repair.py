"""Regression for the dangling Sub_eventID fix: repair_dangling_subevents down-binds a mention whose
sub-event ref is STALE (not in the event key-space) but whose parent EventID RESOLVES — blanking the
stale sub-event id while keeping the valid event-level link. A mention with no resolvable EventID is
a REAL orphan and must be left untouched (surfaced by the audit, not silently masked).
"""

from src.utils.referential_integrity import _repair_record_subevents

VALID = {
    "1HZMC6KKXZEVVB3Q0BEBKDTDNH",
    "7GVTHMZ4YZ1W53FPYAPMD2S5YD",
}  # EventID + a real Sub-eventID


def test_stale_subevent_with_resolving_eventid_is_downbound():
    rec = {
        "event_mentions": [
            {
                "EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH",
                "Sub_eventID": "6QGWBY1B8Z6Q03XWT4ZM75AM98",
            },  # stale sub
        ]
    }
    n = _repair_record_subevents(rec, VALID)
    assert n == 1
    assert rec["event_mentions"][0]["Sub_eventID"] is None  # down-bound
    assert (
        rec["event_mentions"][0]["EventID"] == "1HZMC6KKXZEVVB3Q0BEBKDTDNH"
    )  # parent kept


def test_valid_subevent_untouched():
    rec = {
        "event_mentions": [
            {
                "EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH",
                "Sub_eventID": "7GVTHMZ4YZ1W53FPYAPMD2S5YD",
            },  # valid
        ]
    }
    assert _repair_record_subevents(rec, VALID) == 0
    assert rec["event_mentions"][0]["Sub_eventID"] == "7GVTHMZ4YZ1W53FPYAPMD2S5YD"


def test_real_orphan_eventid_unresolved_left_untouched():
    """No resolvable EventID -> a REAL orphan; must NOT be silently down-bound (audit should see it)."""
    rec = {
        "event_mentions": [
            {
                "EventID": "ZZZZZZZZZZZZZZZZZZZZZZZZZZ",
                "Sub_eventID": "6QGWBY1B8Z6Q03XWT4ZM75AM98",
            },
        ]
    }
    assert _repair_record_subevents(rec, VALID) == 0
    assert (
        rec["event_mentions"][0]["Sub_eventID"] == "6QGWBY1B8Z6Q03XWT4ZM75AM98"
    )  # untouched


def test_dash_variant_and_mixed():
    rec = {
        "event_mentions": [
            {
                "EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH",
                "Sub-eventID": "STALESTALESTALESTALESTALE0",
            },
            {
                "EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH",
                "Sub_eventID": None,
            },  # already event-level
            "not-a-dict",
        ]
    }
    assert _repair_record_subevents(rec, VALID) == 1
    assert rec["event_mentions"][0]["Sub-eventID"] is None


def test_no_event_mentions_safe():
    assert _repair_record_subevents({}, VALID) == 0
    assert _repair_record_subevents({"event_mentions": "nope"}, VALID) == 0


def test_repair_through_real_storage_persists_and_counts(tmp_path):
    """End-to-end (the gap that hid the people/dash defect): drive repair_dangling_subevents through
    the REAL LocalStorage write path (which routes through the schema guard). A down-bound people
    record (dash Sub-eventID -> None) must now PERSIST (schema nullable) and be counted exactly once;
    re-running repairs 0 (idempotent)."""
    import json
    from src.utils.storage import LocalStorage
    from src.utils.referential_integrity import repair_dangling_subevents
    import src.utils.referential_integrity as RI

    out = tmp_path / "output"
    # a valid event key-space (one event + one sub-event)
    ev = out / "content" / "Book"
    ev.mkdir(parents=True)
    (ev / "ch1-event.json").write_text(
        json.dumps(
            {
                "Event": {
                    "EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH",
                    "Sub-events": [{"Sub-eventID": "7GVTHMZ4YZ1W53FPYAPMD2S5YD"}],
                }
            }
        )
    )
    # a people record whose dash Sub-eventID is STALE but EventID resolves
    pdir = out / "people"
    pdir.mkdir(parents=True)
    pf = pdir / "x.json"
    pf.write_text(
        json.dumps(
            {
                "PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW",
                "name": "X",
                "event_mentions": [
                    {
                        "EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH",
                        "Sub-eventID": "STALESTALESTALESTALESTAL00",
                    }
                ],
            }
        )
    )
    st = LocalStorage(base_dir=out)
    n = repair_dangling_subevents(st)
    assert n == 1  # counted (write confirmed, schema now allows null dash Sub-eventID)
    on_disk = json.loads(pf.read_text())
    assert on_disk["event_mentions"][0]["Sub-eventID"] is None  # PERSISTED
    assert on_disk["event_mentions"][0]["EventID"] == "1HZMC6KKXZEVVB3Q0BEBKDTDNH"
    assert repair_dangling_subevents(st) == 0  # idempotent
