"""Tests for v2.4 UnitServed affiliation change (A: GroupID link, B: structured
unit fields) + schema version bump."""

from src.extraction.people import UnitServed, _deduplicate_units
from src.schemas import SCHEMA_VERSION


def test_schema_version_at_least_2_4():
    # v2.4 introduced the UnitServed affiliation fields; the version only moves
    # forward (2.5 added nationality_served + primary_group_id). Assert the floor.
    major, minor = (int(x) for x in SCHEMA_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 4)


def test_unit_served_backcompat():
    # Old records (unit/from/to only) remain valid.
    u = UnitServed(**{"unit": "First Army", "from": "1944-01", "to": "1944-08"})
    assert u.unit == "First Army"
    assert u.GroupID is None and u.designation is None and u.echelon is None


def test_unit_served_structured_and_groupid():
    u = UnitServed(
        **{
            "unit": "9th Infantry Division",
            "designation": "9th Infantry Division",
            "echelon": "division",
            "unit_number": "9",
            "GroupID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
        }
    )
    assert u.echelon == "division"
    assert u.unit_number == "9"
    assert u.GroupID == "01KHXNSE0W41DV7VV6PEMDJJ5H"


def test_dedup_carries_structured_fields():
    units = [
        {
            "unit": "9th Infantry Division",
            "designation": "9th Infantry Division",
            "echelon": "division",
            "unit_number": "9",
        }
    ]
    out = _deduplicate_units(units)
    assert out[0]["echelon"] == "division" and out[0]["unit_number"] == "9"


def test_dedup_merges_dates_and_structured_across_duplicates():
    # One entry has dates, the other has the structured identity → merged result
    # keeps BOTH (this is what enables unit-scoped sourcing on a dated affiliation).
    units = [
        {"unit": "9th Infantry Division", "from": "1944", "to": "1945"},
        {
            "unit": "9th Infantry Division",
            "designation": "9th Infantry Division",
            "echelon": "division",
            "unit_number": "9",
            "GroupID": "01KHXNSE0W41DV7VV6PEMDJJ5H",
        },
    ]
    out = _deduplicate_units(units)
    assert len(out) == 1
    rec = out[0]
    assert rec["from"] == "1944" and rec["to"] == "1945"  # dates preserved
    assert rec["designation"] == "9th Infantry Division"  # structure preserved
    assert rec["echelon"] == "division" and rec["unit_number"] == "9"
    assert rec["GroupID"] == "01KHXNSE0W41DV7VV6PEMDJJ5H"


def test_dedup_drops_none_fields():
    # None-valued optional fields are not written (records stay clean).
    out = _deduplicate_units([{"unit": "First Army", "from": "1944"}])
    assert "GroupID" not in out[0] and "designation" not in out[0]
    assert out[0]["from"] == "1944"
