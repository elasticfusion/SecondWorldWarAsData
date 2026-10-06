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


def test_roman_corps_numbering_unifies_with_arabic():
    # Owner rule: '7th Corps' is a typo for 'VII Corps' — roman unifies with arabic.
    assert derive_unit_key("VII Corps").numbers == derive_unit_key("7th Corps").numbers
    assert _m("VII Corps", "7th Corps")


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


def test_service_branch_veto_pacific():
    # Service (armed service) mismatch is an absolute veto, like nationality.
    assert not _m("1st Marine Division", "1st Division")  # USMC vs ARMY
    assert not _m("1st Marine Division", "1st Infantry Division")
    assert _m("1st Marine Division", "1st Marine Division")  # same service -> ok
    # a Marine unit is NOT defaulted to the Army infantry arm
    k = derive_unit_key("1st Marine Division")
    assert k.service == "USMC" and k.arm is None
    # Navy / air services resolve too
    assert derive_unit_key("Seventh Fleet").service == "USN"
    assert derive_unit_key("8th Air Force").service == "USAAF"


def test_army_is_default_service_with_infantry_arm():
    k = derive_unit_key("9th Division")
    assert k.service == "ARMY" and k.arm == "infantry"


def test_nickname_resolution_to_canonical_key():
    from src.dedup.unit_key import resolve_nickname

    assert resolve_nickname("Screaming Eagles") == "101st Airborne Division"
    assert resolve_nickname("The Screaming Eagles") == "101st Airborne Division"
    assert resolve_nickname("Big Red One") == "1st Infantry Division"
    assert resolve_nickname("Ivy Division") == "4th Infantry Division"
    assert resolve_nickname("not a nickname") is None


def test_nickname_matches_numbered_unit():
    assert _m("Screaming Eagles", "101st Airborne Division")
    assert _m("Big Red One", "1st Division")  # infantry default
    assert _m("Ivy Division", "4th Division")
    assert _m("Third Armored Division", "3rd Armored Division")
    assert _m("Spearhead", "3rd Armored Division")
    # vetoes still protect against false nickname matches
    assert not _m("Screaming Eagles", "82nd Airborne Division")  # 101 != 82
    assert not _m("Big Red One", "1st Armored Division")  # infantry != armored


def test_linker_uses_canonical_key_and_nicknames():
    import importlib.util, sys, tempfile
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "eb_t",
        Path(__file__).parent.parent / "src" / "extraction" / "enrich_biographies.py",
    )
    # import the module normally (it's a package module) to avoid re-exec issues
    import src.extraction.enrich_biographies as eb

    idx = {
        "9th Infantry Division": "g1.json",
        "101st Airborne Division": "g2.json",
        "9th Armored Division": "g3.json",
    }
    d = Path(tempfile.mkdtemp())
    for fn in idx.values():
        (d / fn).write_text("{}")

    def find(u):
        p = eb._find_group_file(u, idx, d)
        return p.name if p else None

    assert find("9th Division") == "g1.json"  # variant -> infantry div
    assert find("Ninth Infantry Division") == "g1.json"
    assert find("Screaming Eagles") == "g2.json"  # nickname
    assert find("9th Armored") == "g3.json"  # arm veto routes correctly
    assert find("bogus unit") is None  # no false link


def test_infantry_default_only_at_division_and_regiment():
    from src.dedup.unit_key import derive_unit_key

    # div/regiment -> infantry default
    assert derive_unit_key("9th Division").arm == "infantry"
    assert derive_unit_key("9th Regiment").arm == "infantry"
    # below regiment -> arm unknown (NOT infantry)
    assert derive_unit_key("9th Battalion").arm is None
    assert derive_unit_key("9th Company").arm is None
    # a bare battalion vs battalion still matches (arm unknown-permissive)
    assert _m("9th Battalion", "9th Battalion")
    # echelon veto still separates battalion from division
    assert not _m("9th Battalion", "9th Infantry Division")


def test_combat_commands_are_armored_and_letter_distinct():
    from src.dedup.unit_key import derive_unit_key

    cca = derive_unit_key("CCA, 3rd Armored Division")
    ccb = derive_unit_key("CCB, 3rd Armored Division")
    assert cca.echelon == "combat_command" and cca.arm == "armored"
    assert "cca" in cca.numbers and "ccb" in ccb.numbers
    # different command letter -> different units
    assert not _m("CCA, 3rd Armored", "CCB, 3rd Armored")
    # same command, abbreviated vs full -> match
    assert _m("CCB, 3rd Armored", "CCB, 3rd Armored Division")
    # "Combat Command B" spelled out resolves the same
    assert derive_unit_key("Combat Command B").echelon == "combat_command"
    assert "ccb" in derive_unit_key("Combat Command B").numbers


def test_combat_command_requires_parent_division():
    # A CC must be affiliated with a division; a bare CC (letter only) is underspecified
    # and must NOT confidently match another bare CC (could be different divisions).
    assert not _m("Combat Command B", "Combat Command B")
    assert not _m("CCB", "CCB")
    # divisioned CCs: same division+letter match; different division/letter don't.
    assert _m("CCB, 3rd Armored", "CCB, 3rd Armored Division")
    assert not _m("CCB, 3rd Armored", "CCB, 7th Armored")
    assert not _m("CCA, 3rd Armored", "CCB, 3rd Armored")
    # a bare CC does not match a divisioned CC either.
    assert not _m("Combat Command B", "CCB, 3rd Armored Division")


def test_infantry_default_is_us_only():
    from src.dedup.unit_key import derive_unit_key

    # US bare division -> infantry
    assert derive_unit_key("9th Division").arm == "infantry"
    # non-US signals suppress the infantry default (German/SS/British/Volksgrenadier)
    assert derive_unit_key("2nd German Division").arm is None
    assert derive_unit_key("1st SS Division").arm is None
    assert derive_unit_key("British 3rd Division").arm is None
    assert (
        derive_unit_key("18 VG Division").arm == "volksgrenadier"
    )  # explicit non-inf modifier


def test_unknown_branch_modifier_vetoes_vs_infantry():
    # "5th Fighter Division" must NOT merge with "5th Infantry Division"
    assert not _m("5th Fighter Division", "5th Infantry Division")
    assert not _m("5th Alpini Division", "5th Infantry Division")
    # but a US bare division still matches the explicit infantry division
    assert _m("9th Division", "9th Infantry Division")
    assert _m(
        "Ninth Division", "9th Division"
    )  # ordinal word not mistaken for a branch


def test_map_shorthand_arm_abbreviations():
    """Map tactical shorthand (CAV/AD/PZ) expands so the combat arm is captured —
    without this a bare '14 CAV' loses its arm and over-matches any 14th unit."""
    assert derive_unit_key("14 CAV").arm == "cavalry"
    assert derive_unit_key("7 AD").arm == "armored"
    assert derive_unit_key("7 AD").echelon == "division"
    assert derive_unit_key("5 PZ Div").arm == "armored"  # panzer -> armored arm
    # arm now discriminates: 14th Cavalry != a 14th infantry/armored unit
    assert not _m("14 CAV", "14th Infantry Division")
    # AD must only expand as a whole word (not inside other tokens)
    assert "armored" in __import__("src.dedup.unit_key", fromlist=["_expand"])._expand(
        "7 AD"
    )


def test_higher_roman_corps_numerals():
    """WWII corps numerals above XX (LXVI=66, LVIII=58, XLVII=47) parse — the small
    table only reached XX, dropping German/US corps designations."""
    assert derive_unit_key("LXVI Corps").numbers == frozenset({"66"})
    assert derive_unit_key("LVIII Panzer Corps").numbers == frozenset({"58"})
    assert derive_unit_key("XLVII Corps").numbers == frozenset({"47"})
    assert derive_unit_key("VII Corps").numbers == frozenset({"7"})  # low still works


def test_roman_parser_rejects_common_words():
    """The general roman fallback must only fire on valid canonical numerals, never on
    ordinary words that happen to use roman letters."""
    from src.dedup.unit_key import _parse_roman

    for w in ("div", "mix", "mild", "civil", "lid", "did", "mid", "dim"):
        assert _parse_roman(w) is None, w
    assert _parse_roman("lxvi") == 66
    assert _parse_roman("mcm") == 1900 or _parse_roman("mcm") is None  # >399 -> None
