"""Casualties Phase A cross-reference upgrades: shared-library resolution."""

from src.extraction.casualties import _resolve_equipment, _resolve_date


def test_equipment_resolves_via_disambiguator_and_index():
    idx = {"m4 sherman": "01EQSHERMAN0000000000000AA"}
    r = _resolve_equipment([{"name": "Sherman", "relation": "causative"}], idx)
    assert r[0]["EquipmentID"] == "01EQSHERMAN0000000000000AA"
    assert r[0]["relation"] == "causative"


def test_equipment_unresolved_is_null_not_fabricated():
    r = _resolve_equipment([{"name": "ambulance", "relation": "medical"}], {})
    assert r == [{"EquipmentID": None, "name": "ambulance", "relation": "medical"}]


def test_date_interval_link_matches_approximate_record():
    # dates_index as _build_date_id_lookup emits: date_start -> {DateID, resolved_*}
    idx = {
        "mid-December 1944": {
            "DateID": "01DATEMIDDEC00000000000000",
            "resolved_earliest": "1944-12-14",
            "resolved_latest": "1944-12-18",
            "time_source": "Allied",
        }
    }
    # a casualty on a specific in-interval day links to the approximate record
    res = _resolve_date("1944-12-16", idx)
    assert res["DateID"] == "01DATEMIDDEC00000000000000"
    assert res["time_source"] == "Allied"


def test_date_unmatched_keeps_string_null_id():
    res = _resolve_date("1943-01-01", {})
    assert res["DateID"] is None and res["date_string"] == "1943-01-01"
