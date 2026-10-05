"""Civilian group_affiliations link into group member lists, carrying date_verified
(unverified title membership -> provisional member, lower confidence)."""

import json

import src.extraction.enrich_biographies as EB


def _setup(tmp_path):
    gdir = tmp_path / "people_groups"
    gdir.mkdir()
    (gdir / "house.json").write_text(
        json.dumps(
            {
                "GroupID": "01H",
                "group_name": "House of Representatives",
                "name": "House of Representatives",
                "members": [],
            }
        )
    )
    (gdir / "div.json").write_text(
        json.dumps(
            {
                "GroupID": "019",
                "group_name": "9th Infantry Division",
                "name": "9th Infantry Division",
                "members": [],
            }
        )
    )
    (gdir / "index.json").write_text(
        json.dumps(
            {
                "House of Representatives": "house.json",
                "9th Infantry Division": "div.json",
            }
        )
    )
    return gdir


def test_unverified_membership_links_as_provisional(tmp_path):
    gdir = _setup(tmp_path)
    pf = tmp_path / "p.json"
    pf.write_text(
        json.dumps(
            {
                "PersonID": "01P",
                "name": "John Smith",
                "biographical_profile": {
                    "units_served": [{"unit": "9th Division"}],
                    "group_affiliations": [
                        {
                            "group": "House of Representatives",
                            "group_kind": "legislature",
                            "implied_from_title": True,
                            "date_verified": False,
                            "as_of_source_date": "1943",
                        }
                    ],
                },
            }
        )
    )
    n = EB._link_person_to_groups(pf, gdir)
    assert n == 2  # unit + civilian

    house = json.loads((gdir / "house.json").read_text())["members"][0]
    assert house["membership_kind"] == "legislature"
    assert house["date_verified"] is False
    assert house["confidence"] == 0.3  # provisional

    div = json.loads((gdir / "div.json").read_text())["members"][0]
    assert div["confidence"] == 0.8  # unit member unchanged
    assert "date_verified" not in div


def test_verified_membership_links_full_confidence(tmp_path):
    gdir = _setup(tmp_path)
    pf = tmp_path / "p.json"
    pf.write_text(
        json.dumps(
            {
                "PersonID": "01Q",
                "name": "Jane Roe",
                "biographical_profile": {
                    "group_affiliations": [
                        {
                            "group": "House of Representatives",
                            "group_kind": "legislature",
                            "implied_from_title": True,
                            "date_verified": True,
                        }
                    ]
                },
            }
        )
    )
    EB._link_person_to_groups(pf, gdir)
    house = json.loads((gdir / "house.json").read_text())["members"][0]
    assert house["date_verified"] is True
    assert house["confidence"] == 0.8  # verified -> full


def test_person_with_only_affiliations_still_links(tmp_path):
    gdir = _setup(tmp_path)
    pf = tmp_path / "p.json"
    pf.write_text(
        json.dumps(
            {
                "PersonID": "01R",
                "name": "No Units",
                "biographical_profile": {
                    "group_affiliations": [
                        {"group": "House of Representatives", "date_verified": True}
                    ]
                },
            }
        )
    )
    assert EB._link_person_to_groups(pf, gdir) == 1  # didn't bail on empty units
