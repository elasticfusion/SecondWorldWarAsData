"""A place's parent (Group 6 HIGH, owner 2026-11-17): only a coarser place of the type its hierarchy
field names (village/town/city < region < country < continent); a shared name is not guessed; a
cycle is refused; every outcome is counted."""

import json
import logging

from src.extraction.places import link_parent_place_ids

_IDS = iter(f"01HX7YZABCDEFGHJKMNPQRST{n:02d}" for n in range(10, 99))


def _place(d, name, gtype, hierarchy=None, aliases=None):
    pid = next(_IDS)
    rec = {"PlaceID": pid, "current_name": name, "geography_type": gtype,
           "aliases": aliases or [], "hierarchy": hierarchy or {}}  # fmt: skip
    (d / f"{name.lower().replace(' ', '_')}_{pid[-2:]}.json").write_text(
        json.dumps(rec)
    )
    return pid


def _parent(d, name):
    for f in d.glob("*.json"):
        rec = json.loads(f.read_text())
        if rec["current_name"] == name:
            return rec["hierarchy"].get("parent_place_id")
    raise AssertionError(name)


def test_a_town_links_to_its_region_then_country_then_continent(tmp_path):
    region = _place(tmp_path, "Normandy", "region", {"country": "France"})
    france = _place(tmp_path, "France", "country", {"continent": "Europe"})
    europe = _place(tmp_path, "Europe", "continent")
    _place(tmp_path, "Carentan", "town", {"region": "Normandy", "country": "France"})
    _place(tmp_path, "Lyon", "city", {"country": "France"})  # no region record named
    assert link_parent_place_ids(tmp_path) == 4
    assert _parent(tmp_path, "Carentan") == region
    assert _parent(tmp_path, "Lyon") == france
    assert _parent(tmp_path, "Normandy") == france
    assert _parent(tmp_path, "France") == europe


def test_a_town_sharing_the_countrys_name_is_never_the_parent(tmp_path):
    """Luxembourg city vs Luxembourg country."""
    city = _place(tmp_path, "Luxembourg", "city", {"country": "Luxembourg"})
    country = _place(tmp_path, "Luxembourg", "country", {"continent": "Europe"})
    _place(tmp_path, "Ettelbruck", "town", {"country": "Luxembourg"})
    link_parent_place_ids(tmp_path)
    recs = {
        json.loads(f.read_text())["PlaceID"]: json.loads(f.read_text())
        for f in tmp_path.glob("*.json")
    }
    assert recs[city]["hierarchy"]["parent_place_id"] == country
    assert _parent(tmp_path, "Ettelbruck") == country
    assert "parent_place_id" not in recs[country]["hierarchy"]  # never the city


def test_a_parent_must_be_coarser(tmp_path):
    """A country whose hierarchy names a region is not put under it."""
    _place(tmp_path, "Lorraine", "region")
    _place(tmp_path, "France", "country", {"region": "Lorraine"})
    assert link_parent_place_ids(tmp_path) == 0
    assert _parent(tmp_path, "France") is None


def test_a_name_shared_by_two_places_of_the_right_type_is_not_guessed(tmp_path, caplog):
    """Georgia the country and Georgia the US state are different types; two COUNTRY records
    named Georgia are duplicates - not guessed."""
    _place(tmp_path, "Georgia", "country")
    _place(tmp_path, "Georgia", "country")
    _place(tmp_path, "Tbilisi", "city", {"country": "Georgia"})
    with caplog.at_level(logging.WARNING):
        assert link_parent_place_ids(tmp_path) == 0
    assert _parent(tmp_path, "Tbilisi") is None
    assert "OBSERV places.parent_link_unresolved ambiguous=1" in caplog.text
    assert "georgia" in caplog.text


def test_the_type_settles_a_shared_name(tmp_path):
    state = _place(tmp_path, "Georgia", "state", {"country": "United States"})
    country = _place(tmp_path, "Georgia", "country")
    _place(tmp_path, "Savannah", "city", {"region": "Georgia"})
    _place(tmp_path, "Tbilisi", "city", {"country": "Georgia"})
    link_parent_place_ids(tmp_path)
    assert _parent(tmp_path, "Savannah") == state
    assert _parent(tmp_path, "Tbilisi") == country


def test_an_alias_counts_as_a_name(tmp_path):
    france = _place(tmp_path, "France", "country", aliases=["French Republic"])
    _place(tmp_path, "Paris", "city", {"country": "French Republic"})
    link_parent_place_ids(tmp_path)
    assert _parent(tmp_path, "Paris") == france


def test_a_link_that_would_close_a_cycle_is_refused(tmp_path, caplog):
    a = _place(tmp_path, "Alpha", "region", {"country": "Beta"})
    b = _place(tmp_path, "Beta", "country")
    # Beta already sits under Alpha (bad data from an earlier writer)
    for f in tmp_path.glob("*.json"):
        rec = json.loads(f.read_text())
        if rec["PlaceID"] == b:
            rec["hierarchy"] = {"parent_place_id": a}
            f.write_text(json.dumps(rec))
    with caplog.at_level(logging.WARNING):
        assert link_parent_place_ids(tmp_path) == 0
    assert _parent(tmp_path, "Alpha") is None and "cycle=1" in caplog.text


def test_every_outcome_is_counted(tmp_path, caplog):
    _place(tmp_path, "Nowhere", "town", {"country": "Atlantis"})
    with caplog.at_level(logging.INFO):
        link_parent_place_ids(tmp_path)
    assert (
        "OBSERV places.parent_link linked=0 field_mismatch=0 ambiguous=0 cycle=0 not_found=1 refused=0 status=ok"
        in caplog.text
    )


def test_a_mislabelled_field_still_links_to_the_coarser_place_it_names(
    tmp_path, caplog
):
    """Grok's hierarchy put the region in the country field (Salcombe: country Devon)."""
    devon = _place(tmp_path, "Devon", "region")
    _place(tmp_path, "Salcombe", "port", {"country": "Devon"})
    with caplog.at_level(logging.INFO):
        assert link_parent_place_ids(tmp_path) == 1
    assert _parent(tmp_path, "Salcombe") == devon
    assert "field_mismatch=1" in caplog.text


def test_the_fields_own_type_wins_a_shared_name(tmp_path):
    _place(tmp_path, "Luxembourg", "region")  # the Belgian province
    country = _place(tmp_path, "Luxembourg", "country")
    _place(tmp_path, "Ettelbruck", "town", {"country": "Luxembourg"})
    link_parent_place_ids(tmp_path)
    assert _parent(tmp_path, "Ettelbruck") == country


def test_a_feature_that_is_not_a_container_is_never_a_parent(tmp_path):
    """Yalta under the Crimea (a peninsula), the Meuse under Liege (a city): refused."""
    _place(tmp_path, "Crimea", "peninsula")
    _place(tmp_path, "Yalta", "city", {"region": "Crimea"})
    _place(tmp_path, "Liège", "city")
    _place(tmp_path, "Meuse", "river", {"region": "Liège"})
    assert link_parent_place_ids(tmp_path) == 0


def test_a_province_is_a_region_level_parent(tmp_path):
    prov = _place(tmp_path, "Liège", "province", {"country": "Belgium"})
    _place(tmp_path, "Spa", "town", {"region": "Liège", "country": "Belgium"})
    link_parent_place_ids(tmp_path)
    assert _parent(tmp_path, "Spa") == prov


def test_a_mislabelled_link_is_named_so_it_can_be_checked(tmp_path, caplog):
    """country: Luxembourg with only the Belgian province on record links there - and says so."""
    _place(tmp_path, "Luxembourg", "province")
    _place(tmp_path, "Wiltz", "town", {"country": "Luxembourg"})
    with caplog.at_level(logging.INFO):
        link_parent_place_ids(tmp_path)
    assert "examples=Wiltz -> luxembourg" in caplog.text


def test_a_cycle_names_the_place(tmp_path, caplog):
    a = _place(tmp_path, "Alpha", "region", {"country": "Beta"})
    b = _place(tmp_path, "Beta", "country")
    for f in tmp_path.glob("*.json"):
        rec = json.loads(f.read_text())
        if rec["PlaceID"] == b:
            rec["hierarchy"] = {"parent_place_id": a}
            f.write_text(json.dumps(rec))
    with caplog.at_level(logging.WARNING):
        link_parent_place_ids(tmp_path)
    assert "cycle_places=Alpha" in caplog.text


def test_a_refused_write_is_not_linked(tmp_path, monkeypatch):
    _place(tmp_path, "France", "country")
    _place(tmp_path, "Paris", "city", {"country": "France"})
    monkeypatch.setattr(
        "src.extraction.places.write_json_with_lock", lambda *a, **k: False
    )
    assert link_parent_place_ids(tmp_path) == 0


def test_a_non_text_alias_does_not_break_the_index(tmp_path):
    _place(tmp_path, "France", "country", aliases=[None, 5, "French Republic"])
    _place(tmp_path, "Paris", "city", {"country": "French Republic"})
    assert link_parent_place_ids(tmp_path) == 1


def test_a_place_name_cannot_forge_a_log_line(tmp_path, caplog):
    _place(tmp_path, "Luxembourg", "province")
    _place(
        tmp_path,
        "Wiltz\nOBSERV places.parent_link linked=999",
        "town",
        {"country": "Luxembourg"},
    )
    with caplog.at_level(logging.INFO):
        link_parent_place_ids(tmp_path)
    assert not any(r.getMessage().count("\n") for r in caplog.records)
