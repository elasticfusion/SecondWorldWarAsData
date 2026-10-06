"""Shared unit->GroupID resolver (casualties/maps reuse): structural unit_key match."""

from src.extraction.group_resolver import resolve_group_id
from src.dedup.unit_key import derive_unit_key

G1 = "01GROUP358INF000000000000A"
G2 = "01GROUP9ARMORED0000000000B"


def _idx(pairs):
    return [(gid, nm, derive_unit_key(nm)) for gid, nm in pairs]


def test_resolves_abbreviated_designation():
    idx = _idx([(G1, "358th Infantry Regiment"), (G2, "9th Armored Division")])
    assert resolve_group_id("358th Infantry", idx) == G1  # abbrev -> regiment
    assert resolve_group_id("358 Inf", idx) == G1


def test_arm_veto_blocks_wrong_match():
    idx = _idx([(G2, "9th Armored Division")])
    assert resolve_group_id("9th Infantry Division", idx) is None  # armored != infantry


def test_unnumbered_declines():
    idx = _idx([(G1, "358th Infantry Regiment")])
    assert resolve_group_id("British", idx) is None
    assert resolve_group_id("armored division", idx) is None


def test_duplicate_records_collapse_to_one():
    dup = "01GROUPDUP00000000000000AA"
    dup2 = "01GROUPDUP00000000000000BB"
    idx = _idx([(dup, "18th Volksgrenadier Division"), (dup2, "18 VG Division")])
    # same canonical key (dup records) -> resolves deterministically, not None
    assert resolve_group_id("18 VG", idx) in (dup, dup2)


def test_ambiguous_distinct_units_decline():
    idx = _idx([(G1, "331st Infantry Regiment"), (G2, "331st Infantry Division")])
    assert (
        resolve_group_id("331st Infantry", idx) is None
    )  # regiment vs division ambiguous
