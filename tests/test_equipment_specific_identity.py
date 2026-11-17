"""_identity_scope: alias-aware (nicknames are specific) + category/subcategory classification
phrases ('medium tank' vs 'M4') and bare calibers are GENERIC (kept in the JSON, never searched).
A technical_identifier counts only when it is a real designation, not the bare name copied.
"""

import pytest

from src.extraction.equipment import _canonical_equipment_name, _identity_scope


@pytest.fixture(autouse=True)
def _no_disambiguation_writes(monkeypatch, tmp_path):
    """Resolutions must never write the real learned-alias store or suggestions file."""
    from src.extraction import equipment_disambiguation as ED

    monkeypatch.setattr(ED, "_persist_learned", lambda *a, **k: None)
    monkeypatch.setattr(ED, "_SUGGESTIONS_PATH", tmp_path / "suggestions.jsonl")
    ED._CACHE.clear()
    yield
    ED._CACHE.clear()


class _NoGrok:
    """Canonical resolver finds nothing (Grok returns no designation)."""

    def extract_json(self, *a, **k):
        return {}


def _scope(**rec):
    return _identity_scope(rec, _NoGrok())


def test_technical_identifier_designation_is_specific():
    assert _scope(common_name="whatever", technical_identifier="M4") == "specific"


def test_technical_identifier_copy_of_a_generic_name_is_not_evidence():
    # XChannel-8: the extractor filled technical_identifier with the bare name for 50/53 records
    assert _scope(common_name="trench", technical_identifier="trench") == "generic"
    assert _scope(common_name="rifle", technical_identifier="rifle") == "generic"


def test_nickname_resolves_specific():
    for name in ("Sherman", "Pershing", "88", "Sherman tank", "Tommy gun"):
        assert _scope(common_name=name) == "specific", name


def test_bare_generic_not_specific():
    for name in ("tank", "gun", "cannon", "howitzer", "rifle"):
        assert _scope(common_name=name) == "generic", name


@pytest.mark.parametrize(
    "cls",
    [
        "medium tank",
        "Medium Tank",
        "medium_tank",
        "heavy tank",
        "field gun",
        "tank destroyer",
        "fighter-bomber",
        "self-propelled gun",
    ],
)
def test_category_phrase_not_specific(cls):
    # the "medium tank vs M4" disambiguation: the class is NOT a specific identity
    assert _scope(common_name=cls) == "generic"


@pytest.mark.parametrize(
    "name",
    ["105-mm howitzer", "Russian 122-mm gun", "75-mm antitank gun", "155-mm gun"],
)
def test_bare_caliber_is_generic(name):
    # owner decision: a caliber without a model is generic (upgrading it would be guessing)
    assert _scope(common_name=name) == "generic"


@pytest.mark.parametrize(
    "name",
    [
        "M4 Sherman",
        "Tiger I",
        "Panzer IV",
        "B-24",
        "Bf 109",
        "LCVP",
        "LCI(L)",
        "DUKW",
        "E-boat",
    ],
)
def test_specific_designation_is_specific(name):
    assert _scope(common_name=name) == "specific"


def test_ship_type_word_is_generic_even_capitalised():
    assert _scope(
        common_name="Minesweeper", category="naval", subcategory="minesweeper"
    ) == ("generic")


def test_grok_resolution_needs_confidence(monkeypatch, tmp_path):
    class _Grok:
        def __init__(self, conf):
            self.conf = conf

        def extract_json(self, *a, **k):
            return {"canonical_name": "Zephyr Mk Test Gun", "confidence": self.conf}

    from src.extraction import equipment_disambiguation as ED

    # never write the real learned-alias store / suggestions file from a test
    monkeypatch.setattr(ED, "_persist_learned", lambda *a, **k: None)
    monkeypatch.setattr(ED, "_SUGGESTIONS_PATH", tmp_path / "s.jsonl")
    name = "zephyr widget"  # in no alias table: the Grok answer decides
    ED._CACHE.clear()
    assert _identity_scope({"common_name": name}, _Grok(0.95)) == "specific"
    ED._CACHE.clear()
    assert _identity_scope({"common_name": name}, _Grok(0.5)) == "generic"
    ED._CACHE.clear()


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
