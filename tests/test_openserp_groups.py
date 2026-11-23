"""Tests for people_groups (unit/formation) OpenSERP enrichment."""

import json
from unittest.mock import patch

import jsonschema

import src.enrichment.openserp_enrichment as oe
from src.schemas.groups_output import GROUPS_OUTPUT_SCHEMA


def test_group_queries_are_wwii_scoped_and_disambiguated(tmp_path, queued_finds):
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

    def fake_search(q, url, limit=10, start=0):
        seen.append(q)
        return [
            {
                "url": f"http://example.org/{abs(hash(q)) % 1000}",
                "title": "101st Airborne Division WWII",
            }
        ]

    with (
        patch.object(oe, "_openserp_reachable", return_value=True),
        patch.object(oe, "_search_openserp", side_effect=fake_search),
        patch.object(oe, "_verify_result", return_value=True),
        patch(
            "src.extraction.enrich_biographies._fetch_url_content",
            return_value="<p>101st Airborne Division WWII unit history.</p>",
        ),
        patch.object(oe, "_summarize_url_page", return_value="Unit history summary."),
    ):
        n = oe.enrich_groups_with_openserp(gd, "http://x")

    assert n == 1
    # every query must be WWII-scoped (no same-numbered unit from another conflict)
    assert all(("wwii" in q.lower() or "world war ii" in q.lower()) for q in seen)
    # nationality disambiguation + veterans-association coverage
    assert any("USA" in q for q in seen)
    assert any("veterans association" in q.lower() for q in seen)

    g = json.loads((gd / "g.json").read_text())
    assert g["images"]
    # pages are held in the review queue, written to no record (owner 2026-11-17)
    assert "web_results" not in g and "veterans_associations" not in g
    finds = queued_finds()
    assert finds and all(e["review_status"] == "pending" for e in finds)
    assert {e["request_params"]["origin_entity_id"] for e in finds} == {
        "01HX7YZABCDEFGHJKMNPQRSTVW"
    }
    assert {e["request_params"]["category"] for e in finds} == {
        "web_results",
        "veterans_association",
    }
    assert g["openserp_searched"] is True
    jsonschema.validate(g, GROUPS_OUTPUT_SCHEMA)


def test_group_openserp_dedupes(tmp_path, queued_finds):
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
            return_value=[
                {"url": "http://example.org/dup", "title": "9th Infantry Division WWII"}
            ],
        ),
        patch.object(oe, "_verify_result", return_value=True),
        patch.object(
            oe,
            "process_positive_url",
            return_value={
                "url": "http://example.org/dup",
                "title": "t",
                "summary": "s",
            },
        ),
    ):
        oe.enrich_groups_with_openserp(gd, "http://x")
    g = json.loads((gd / "g.json").read_text())
    assert [r["url"] for r in g["web_results"]] == ["http://dup"]  # no page added
    assert len([e for e in queued_finds() if "example.org/dup" in e["endpoint"]]) == 1


def test_a_second_run_queues_no_duplicates_and_spends_nothing(queued_finds):
    data = {
        "GroupID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "group_name": "9th Infantry Division",
        "nationality": "USA",
    }
    page = {
        "url": "http://example.org/dup",
        "title": "t",
        "summary": "s",
        "source": "openserp",
    }
    with (
        patch.object(
            oe,
            "_search_openserp",
            return_value=[
                {"url": "http://example.org/dup", "title": "9th Infantry Division WWII"}
            ],
        ),
        patch.object(oe, "_verify_result", return_value=True),
        patch.object(oe, "process_positive_url", return_value=page) as proc,
    ):
        assert oe.enrich_one_group(data, "http://x", grok_client=object()) is True
        first = len(queued_finds())
        calls = proc.call_count
        assert oe.enrich_one_group(data, "http://x", grok_client=object()) is False
    assert (
        len(queued_finds()) == first and proc.call_count == calls
    )  # no fetch, no Grok
