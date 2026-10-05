"""Canonical unit key for group dedup: Ninth/9th/9th-Infantry Division match; branch +
echelon vetoes; abbreviations; infantry default."""

from src.dedup.unit_key import derive_unit_key, unit_keys_match


def _m(a, b):
    return unit_keys_match(derive_unit_key(a), derive_unit_key(b))[0]


def test_ninth_variants_all_match():
    assert _m("Ninth Division", "9th Division")
    assert _m("9th Division", "9th Infantry Division")
    assert _m("Ninth Division", "9th Infantry Division")


def test_infantry_default_vs_explicit_branch_veto():
    # "9th Division" defaults to infantry; "9th Armored" is armored -> different units
    assert not _m("9th Armored", "9th Division")
    assert not _m("9th Infantry Division", "9th Armored Division")


def test_echelon_mismatch_veto_but_absent_is_permissive():
    assert not _m("9th Division", "9th Regiment")  # both present, differ -> veto
    assert _m("9th Armored", "9th Armored Division")  # echelon absent on one -> ok
    assert _m("110th", "110th Regiment")  # bare number -> permissive


def test_number_must_match():
    assert not _m("9th Division", "10th Division")
    assert not _m("Division", "Division")  # no number -> no match


def test_abbreviations_expand():
    assert _m("502nd PIR", "502nd Parachute Infantry Regiment")
    assert _m("1st Inf Div", "1st Infantry Division")
    assert _m("3rd Armd Div", "3rd Armored Division")


def test_roman_corps_numbering_distinct_from_arabic():
    # VII Corps (roman 7) should not match 7th Corps (arabic) by number key
    k_roman = derive_unit_key("VII Corps")
    k_arabic = derive_unit_key("7th Corps")
    assert k_roman.numbers != k_arabic.numbers


def test_group_nationality_veto():
    import importlib.util, sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "fdg_t", Path(__file__).parent.parent / "scripts" / "find_duplicate_groups.py"
    )
    fdg = importlib.util.module_from_spec(spec)
    sys.modules["fdg_t"] = fdg
    spec.loader.exec_module(fdg)

    def g(name, nat=None):
        return {
            "name": name,
            "data": {"name": name, "nationality": nat} if nat else {"name": name},
        }

    # same designation, different nationality -> VETO
    assert fdg._group_nationality_conflict(
        g("2nd Division", "USA"), g("2nd Division", "Canada")
    )
    # name-embedded nationality
    assert fdg._group_nationality_conflict(
        g("2nd Division (Canadian)"), g("2nd Division (US)")
    )
    # unknown on one side -> permissive
    assert not fdg._group_nationality_conflict(
        g("2nd Division", "USA"), g("2nd Division")
    )
    # same nationality -> no veto
    assert not fdg._group_nationality_conflict(
        g("2nd Division", "USA"), g("2nd Infantry Division", "USA")
    )
