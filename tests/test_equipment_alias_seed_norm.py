"""Seeded cross-walks + normalization: Sd.Kfz./British marks/A-numbers resolve; surface
variants (Pz.Kpfw. IV == Panzer IV == PzKpfw IV) normalize to one key."""

from src.extraction.equipment import (
    _canonical_equipment_name as canon,
    _normalize_designation as norm,
)


def test_sdkfz_resolves():
    assert canon("Sd.Kfz. 181") == "pzkpfw vi tiger"
    assert canon("sdkfz 171") == "pzkpfw v panther"
    assert (
        canon("Sd.Kfz. 161/2").startswith("pzkpfw iv")
        or canon("sdkfz 161-2") == "pzkpfw iv"
    )


def test_british_sherman_marks():
    assert canon("Sherman V") == "m4a4 sherman"
    assert canon("Sherman III") == "m4a2 sherman"
    assert canon("Firefly") == "sherman firefly"
    assert canon("Sherman IC") == "sherman firefly"


def test_british_a_numbers():
    assert canon("A22") == "churchill"
    assert canon("A27M") == "cromwell"
    assert canon("A34") == "comet"


def test_normalization_unifies_variants():
    assert norm("Pz.Kpfw. IV") == norm("Panzer IV") == norm("PzKpfw IV") == "panzer iv"
    assert norm("Sd.Kfz. 181") == "sdkfz 181"


def test_sdkfz_variants_normalize_same():
    assert canon("Sd. Kfz. 181") == canon("SdKfz 181") == "pzkpfw vi tiger"
