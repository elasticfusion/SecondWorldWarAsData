"""Tests for people_groups (unit/formation) OpenSERP enrichment."""

import json
from unittest.mock import patch

import jsonschema

import src.enrichment.openserp_enrichment as oe
from src.schemas.groups_output import GROUPS_OUTPUT_SCHEMA


def test_group_queries_are_wwii_scoped_and_disambiguated(tmp_path):
    gd = tmp_path / "people_groups"
    gd.mkdir()
    (gd / "g.json").write_text(
        json.dumps(
            {
                "GroupID": "01HX7YZABCDEFGHJKMNPQRSTVW",
                "group_name": "101st Airborne Division",
                "nationality": "USA",
            }
        )
    )
    seen = []

    def fake_search(q, url, limit=10):
        seen.append(q)
        return [
            {
                "url": f"http://{abs(hash(q)) % 1000}",
                "title": "101st Airborne Division WWII",
            }
        ]

    with (
        patch.object(oe, "_openserp_reachable", return_value=True),
        patch.object(oe, "_search_openserp", side_effect=fake_search),
        patch.object(oe, "_verify_result", return_value=True),
    ):
        n = oe.enrich_groups_with_openserp(gd, "http://x")

    assert n == 1
    # every query must be WWII-scoped (no same-numbered unit from another conflict)
    assert all(("wwii" in q.lower() or "world war ii" in q.lower()) for q in seen)
    # nationality disambiguation + veterans-association coverage
    assert any("USA" in q for q in seen)
    assert any("veterans association" in q.lower() for q in seen)

    g = json.loads((gd / "g.json").read_text())
    assert g["images"] and g["web_results"] and g["veterans_associations"]
    assert g["openserp_searched"] is True
    jsonschema.validate(g, GROUPS_OUTPUT_SCHEMA)


def test_group_openserp_dedupes(tmp_path):
    gd = tmp_path / "people_groups"
    gd.mkdir()
    (gd / "g.json").write_text(
        json.dumps(
            {
                "GroupID": "01HX7YZABCDEFGHJKMNPQRSTVW",
                "group_name": "9th Infantry Division",
                "nationality": "USA",
                "web_results": [{"url": "http://dup", "source": "openserp"}],
            }
        )
    )
    with (
        patch.object(oe, "_openserp_reachable", return_value=True),
        patch.object(
            oe,
            "_search_openserp",
            return_value=[{"url": "http://dup", "title": "9th Infantry Division WWII"}],
        ),
        patch.object(oe, "_verify_result", return_value=True),
    ):
        oe.enrich_groups_with_openserp(gd, "http://x")
    g = json.loads((gd / "g.json").read_text())
    urls = [r["url"] for r in g["web_results"]]
    assert urls.count("http://dup") == 1
