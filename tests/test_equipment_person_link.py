"""Hardened using_person/using_unit linking: conservative resolution; an ambiguous bare
name (multiple matching index entries) must NOT silently grab the wrong person."""

from src.extraction.equipment import _link_entity

P1 = "01PERSON100000000000000000"
P2 = "01PERSON200000000000000000"


def test_exact_match():
    r = _link_entity("John Smith", {"John Smith": P1}, "person")
    assert r["PersonID"] == P1 and r["name"] == "John Smith"


def test_case_insensitive():
    r = _link_entity("john smith", {"John Smith": P1}, "person")
    assert r["PersonID"] == P1


def test_unambiguous_containment_links():
    # "Sergeant Smith" -> the one "Sergeant John Smith" (whole-phrase, single candidate)
    r = _link_entity("Sergeant Smith", {"Sergeant John Smith (Smith)": P1}, "person")
    # containment on the stated phrase; single candidate -> link
    r2 = _link_entity("John Smith", {"Sgt John Smith": P1}, "person")
    assert r2 and r2["PersonID"] == P1


def test_ambiguous_bare_name_no_link():
    # two Smiths -> a bare "Smith" is ambiguous -> NO link (don't grab the wrong one)
    r = _link_entity("Smith", {"John Smith": P1, "William Smith": P2}, "person")
    assert r is None


def test_distant_rejected():
    assert _link_entity("Eisenhower", {"John Smith": P1}, "person") is None


def test_fuzzy_typo_links():
    # minor typo, single close candidate -> fuzzy >= 0.88
    r = _link_entity("Sergeant John Smith", {"Sergeant Jon Smith": P1}, "person")
    assert r and r["PersonID"] == P1


def test_unit_uses_peoplegroupid_key():
    r = _link_entity(
        "2nd Armored Division",
        {"2nd Armored Division": "01GRP00000000000000000000A"},
        "unit",
    )
    assert "PeopleGroupID" in r
