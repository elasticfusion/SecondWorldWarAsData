"""Tests for cross-field OpenSERP query enrichment (entity facts sharpen queries)."""

import src.enrichment.openserp_enrichment as oe


def test_person_facts_unit_rank_nationality():
    data = {
        "biographical_profile": {
            "units_served": [{"designation": "90th Infantry Division"}],
            "rank": "Captain",
            "nationality": "American",
        }
    }
    assert oe._person_query_terms(data) == "90th Infantry Division Captain American"


def test_person_facts_empty_when_no_profile():
    assert oe._person_query_terms({}) == ""


def test_equipment_facts_category_and_origin():
    data = {"category": "medium tank", "country_of_origin": "USA"}
    assert oe._equipment_query_terms(data) == "medium tank USA"


def test_render_queries_injects_facts():
    qs = oe._render_queries(
        "people", "portrait_images", name="R Smith", facts="90th Division American"
    )
    assert all("90th Division American" in q for q in qs)
    assert all(
        "R Smith" in q and "WWII" in q.replace("World War II", "WWII") for q in qs
    )


def test_render_queries_empty_facts_collapses():
    # empty facts must not leave double spaces or a dangling {facts} placeholder
    qs = oe._render_queries("people", "portrait_images", name="R Smith", facts="")
    assert all("{facts}" not in q for q in qs)
    assert all("  " not in q for q in qs)
    assert qs[0] == "R Smith WWII portrait photo"


def test_render_queries_equipment_facts():
    qs = oe._render_queries(
        "equipment",
        "images",
        name="M4 Sherman",
        identifier="M4",
        year="1944",
        facts="medium tank USA",
    )
    assert any("medium tank USA" in q for q in qs)
    assert any("M4 Sherman" in q for q in qs)
