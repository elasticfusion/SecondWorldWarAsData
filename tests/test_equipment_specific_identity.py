"""_is_specific_identity: alias-aware (nicknames are specific) + category/subcategory
classification phrases ('medium tank' vs 'M4') are NOT specific."""

from src.extraction.equipment import _is_specific_identity, _canonical_equipment_name


def test_technical_identifier_is_specific():
    assert _is_specific_identity(
        {"common_name": "whatever", "technical_identifier": "M4"}
    )


def test_nickname_resolves_specific():
    assert _is_specific_identity({"common_name": "Sherman"})
    assert _is_specific_identity({"common_name": "Pershing"})
    assert _is_specific_identity({"common_name": "88"})


def test_bare_generic_not_specific():
    assert not _is_specific_identity({"common_name": "tank"})
    assert not _is_specific_identity({"common_name": "gun"})


def test_category_phrase_not_specific():
    # the "medium tank vs M4" disambiguation: the class is NOT a specific identity
    for cls in [
        "medium tank",
        "Medium Tank",
        "medium_tank",
        "heavy tank",
        "field gun",
        "tank destroyer",
        "fighter-bomber",
        "self-propelled gun",
    ]:
        assert not _is_specific_identity({"common_name": cls}), cls


def test_specific_designation_is_specific():
    # Both are specific identities (independent of nationality):
    assert _is_specific_identity({"common_name": "M4 Sherman"})  # US
    assert _is_specific_identity({"common_name": "Tiger I"})  # German — a DISTINCT type


def test_tiger_alias_is_german_not_sherman():
    # Guard: the 'tiger' nickname must resolve to the German Tiger, never a Sherman.
    canonical = _canonical_equipment_name("Tiger")
    assert "sherman" not in canonical.lower()
    assert "tiger" in canonical.lower()


def test_canonical_name_resolves_nickname():
    assert _canonical_equipment_name("Sherman") == "m4 sherman"
    assert (
        _canonical_equipment_name("M4 Sherman") == "M4 Sherman"
    )  # unchanged if not alias
