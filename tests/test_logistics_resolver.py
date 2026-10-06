"""Shared logistics resolver: explicit + inferred co-occurring (GroupID + time) links."""

from src.extraction.logistics_resolver import resolve_logistics

LID = "01LOG0000000000000000000AA"


def _idx():
    entry = {
        "LogisticsID": LID,
        "logistics_type": "supply_shortage",
        "date_start": "1944-12-15",
        "date_end": "1944-12-20",
        "date_ids": ["01DATE000000000000000000AA", None],
    }
    return {"by_id": {LID: entry}, "by_group": {"01GRP00000000000000000000A": [entry]}}


def test_explicit_link():
    r = resolve_logistics([{"LogisticsID": LID}], [], None, _idx())
    assert r == [
        {
            "LogisticsID": LID,
            "logistics_type": "supply_shortage",
            "association": "explicit",
        }
    ]


def test_explicit_unresolved_keeps_null_id():
    r = resolve_logistics([{"description": "no ammo"}], [], None, _idx())
    assert r[0]["LogisticsID"] is None and r[0]["association"] == "explicit"


def test_inferred_via_iso_overlap():
    r = resolve_logistics([], ["01GRP00000000000000000000A"], "1944-12-17", _idx())
    assert r[0]["LogisticsID"] == LID and r[0]["association"] == "co_occurring"


def test_inferred_via_shared_dateid():
    r = resolve_logistics(
        [],
        ["01GRP00000000000000000000A"],
        None,
        _idx(),
        date_id="01DATE000000000000000000AA",
    )
    assert r[0]["association"] == "co_occurring"


def test_out_of_range_no_inferred_link():
    assert (
        resolve_logistics([], ["01GRP00000000000000000000A"], "1944-11-01", _idx())
        == []
    )


def test_supply_only_filter():
    idx = _idx()
    idx["by_group"]["01GRP00000000000000000000A"][0]["logistics_type"] = "supply_excess"
    # supply_excess not in SUPPLY_TYPES -> no inferred link
    assert (
        resolve_logistics([], ["01GRP00000000000000000000A"], "1944-12-17", idx) == []
    )


def test_explicit_not_downgraded_by_inference():
    r = resolve_logistics(
        [{"LogisticsID": LID}], ["01GRP00000000000000000000A"], "1944-12-17", _idx()
    )
    assert len(r) == 1 and r[0]["association"] == "explicit"
