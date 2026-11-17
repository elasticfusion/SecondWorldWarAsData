"""Place dedup must judge names by their START, not their shared ENDINGS.

XChannel-8 E2E (2026-11-07) blocked the dedup gate with 8 false-positive place groups, every one
caused by a shared generic tail: -mouth, -ville, Beach, -au-Plain, -du-Mont. Owner rule: if the
beginning does not match, the ending must not matter at all.
"""

import itertools

import pytest

from src.dedup.find_duplicate_places_v2 import (
    _check_match,
    _distinctive_start,
    _similarity,
    _starts_match,
)

# The exact member lists of the 8 false-positive groups from the run (distinct real places).
FALSE_POSITIVE_GROUPS = [
    ["Beuzeville-au-Plain", "Neuville-au-Plain"],
    ["St. Côme-du-Mont", "Ste. Marie-du-Mont"],
    ["Hiesville", "Houdienville", "Vierville"],
    ["OMAHA Beach", "UTAH Beach"],
    ["Plymouth", "Weymouth"],
    ["Colleville", "Culoville", "Longueville", "Pouppeville"],
    ["Dartmouth", "Falmouth", "Portsmouth"],
    ["Amfreville", "Foucarville", "Turqueville"],
]


def _place(name, lat=None, lon=None):
    return {"name": name, "lat": lat, "lon": lon, "coords_verified": True}


@pytest.mark.parametrize("group", FALSE_POSITIVE_GROUPS)
def test_shared_endings_are_not_duplicates(group):
    for a, b in itertools.combinations(group, 2):
        assert _similarity(a, b) == 0.0, (a, b)
        match, _ = _check_match(_place(a), _place(b))
        assert not match, (a, b)


def test_shared_ending_is_ignored_even_when_the_places_are_close():
    """Proximity must not rescue a name match that has no matching start."""
    match, _ = _check_match(
        _place("Plymouth", 50.37, -4.14), _place("Weymouth", 50.37, -4.14)
    )
    assert not match


@pytest.mark.parametrize(
    "a,b",
    [
        ("Marseille", "Marseilles"),
        ("Caen", "Caen copy"),
        ("Sainte-Mère-Église", "Ste. Mère-Eglise"),
        ("St. Lô", "Saint-Lô"),
        ("Mont-Saint-Michel", "Saint-Michel"),
        ("Carentan", "Carentan-sur-Vire"),
        ("Utah Beach", "UTAH BEACH"),
        ("Grémecey", "Gremecey"),
    ],
)
def test_same_start_still_counts(a, b):
    assert _starts_match(a, b), (a, b)
    assert _similarity(a, b) > 0.0


def test_true_duplicates_still_match_when_start_and_most_of_the_name_agree():
    match, _ = _check_match(
        _place("Marseille", 43.3, 5.37), _place("Marseilles", 43.3, 5.37)
    )
    assert match
    match, _ = _check_match(_place("Sainte-Mère-Église"), _place("Ste. Mère-Eglise"))
    assert match


@pytest.mark.parametrize(
    "name,start",
    [
        ("St. Côme-du-Mont", "come"),
        ("Ste. Marie-du-Mont", "marie"),
        ("Le Havre", "havre"),
        ("OMAHA Beach", "omaha"),
        ("Fort Eben-Emael", "eben"),
        ("Mont", "mont"),  # a lone noise word is still the name
        ("", ""),
    ],
)
def test_distinctive_start(name, start):
    assert _distinctive_start(name) == start


def test_short_names_need_only_their_own_length_in_common():
    assert _starts_match("Lo", "Lo")
    assert not _starts_match("Lo", "Ra")
    assert not _starts_match("", "Caen")


@pytest.mark.parametrize(
    "a,b",
    [
        ("Isigny", "d'Isigny"),
        ("Pont-à-Mousson", "Mousson"),
        ("Mt. Cassino", "Monte Cassino"),
        ("Mount Cassino", "Monte Cassino"),
        ("L'Aigle", "Aigle"),
    ],
)
def test_elided_articles_and_title_variants_do_not_split_a_name(a, b):
    assert _starts_match(a, b), (a, b)


def test_near_identical_pair_zeroed_by_the_gate_is_counted_not_silent():
    import src.dedup.find_duplicate_places_v2 as fp

    fp._GATED_HIGH_SIM[0] = 0
    assert _similarity("Le Havre", "Le Harve") == 0.0  # typo after char 2 -> gated
    assert fp._GATED_HIGH_SIM[0] == 1
    assert (
        _similarity("Plymouth", "Weymouth") == 0.0
    )  # genuinely different: not counted
    assert fp._GATED_HIGH_SIM[0] == 1


def test_non_string_place_names_are_skipped_not_fatal(tmp_path):
    import json

    import src.dedup.find_duplicate_places_v2 as fp

    (tmp_path / "a.json").write_text(json.dumps({"name": {"x": 1}}))
    (tmp_path / "b.json").write_text(json.dumps({"name": 5}))
    (tmp_path / "c.json").write_text(json.dumps({"name": "Caen"}))
    assert [p["name"] for p in fp._load_places(tmp_path)] == ["Caen"]
