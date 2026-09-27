"""Regression test: same invalid ULID -> same replacement (referential integrity).

Guards the property that _fix_invalid_ulids maps each distinct invalid value to
ONE replacement ULID reused everywhere it appears, so internal cross-references
(e.g. EventID referenced by child ref_id fields) stay consistent after fixing.
Previously flagged as a risk (CODE_INTEGRITY_REVIEW #5); this locks the behavior.
"""

from src.utils.json_validator import _fix_invalid_ulids


def test_same_invalid_ulid_gets_same_replacement_across_nesting():
    data = {
        "EventID": "bad-id-XYZ",
        "children": [
            {"ref_id": "bad-id-XYZ"},
            {"ref_id": "bad-id-XYZ"},
        ],
        "PersonID": "another-bad-id",
    }
    out = _fix_invalid_ulids(data)

    # All occurrences of the SAME invalid value collapse to one replacement.
    replaced = {
        out["EventID"],
        out["children"][0]["ref_id"],
        out["children"][1]["ref_id"],
    }
    assert len(replaced) == 1, f"expected one consistent replacement, got {replaced}"

    # A DIFFERENT invalid value gets its own distinct replacement.
    assert out["PersonID"] != out["EventID"]

    # Input is not mutated.
    assert data["EventID"] == "bad-id-XYZ"


def test_valid_ulids_are_left_unchanged():
    valid = "01HQXYZ123456789ABCDEFGHJK"
    data = {"EventID": valid, "child": {"ref_id": valid}}
    out = _fix_invalid_ulids(data)
    assert out["EventID"] == valid
    assert out["child"]["ref_id"] == valid
