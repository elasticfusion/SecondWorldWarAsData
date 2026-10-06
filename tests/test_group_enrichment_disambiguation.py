"""Group enrichment: disambiguated query + wrong-article guard."""

import json

from src.extraction.enrich_groups import (
    _build_enrichment_query,
    _enrichment_agrees,
    enrich_group,
)


def test_query_disambiguated_by_nationality():
    q, _ = _build_enrichment_query({"name": "9th Division", "nationality": "USA"})
    assert q == "9th Division (United States)"
    q2, _ = _build_enrichment_query({"name": "2nd Division", "nationality": "CAN"})
    assert q2 == "2nd Division (Canada)"


def test_query_resolves_nickname():
    q, canon = _build_enrichment_query(
        {"name": "Screaming Eagles", "nationality": "USA"}
    )
    assert canon == "101st Airborne Division"
    assert q == "101st Airborne Division (United States)"


def test_query_plain_when_no_disambiguator():
    q, _ = _build_enrichment_query({"name": "Some Organization"})
    assert q == "Some Organization"


def test_guard_rejects_nationality_contradiction():
    ok, reason = _enrichment_agrees({"nationality": "CAN"}, {"nationality": "USA"})
    assert not ok and "nationality" in reason


def test_guard_rejects_echelon_contradiction():
    ok, _ = _enrichment_agrees(
        {"military_hierarchy": "division"}, {"unit_type": "regiment"}
    )
    assert not ok


def test_guard_permissive_on_unknown_and_agreement():
    assert _enrichment_agrees({"nationality": "USA"}, {"unit_type": "division"})[0]
    assert _enrichment_agrees(
        {"nationality": "USA", "military_hierarchy": "division"},
        {"nationality": "USA", "unit_type": "division"},
    )[0]


class _FakeGrok:
    def __init__(self, result):
        self._r = result

    def extract_json(self, prompt, use_cache=True, cache_type=""):
        return self._r


def test_enrich_group_marks_ambiguous_on_contradiction(tmp_path):
    # Record is a Canadian 2nd Division; Grok returns the US one -> guard -> ambiguous
    gf = tmp_path / "g.json"
    gf.write_text(json.dumps({"name": "2nd Division", "nationality": "CAN"}))
    grok = _FakeGrok(
        {"nationality": "USA", "unit_type": "division", "description": "US unit"}
    )
    out = enrich_group(gf, grok)
    data = json.loads(gf.read_text())
    assert out is False
    assert data["enrichment_status"] == "ambiguous"
    assert "nationality" in data["enrichment_ambiguity_reason"]
    assert "enrichment_data" not in data  # wrong data NOT stamped


def test_enrich_group_accepts_agreeing(tmp_path):
    gf = tmp_path / "g.json"
    gf.write_text(json.dumps({"name": "1st Infantry Division", "nationality": "USA"}))
    grok = _FakeGrok(
        {"nationality": "USA", "unit_type": "division", "description": "Big Red One"}
    )
    out = enrich_group(gf, grok)
    data = json.loads(gf.read_text())
    assert out is True
    assert data["enrichment_status"] == "enriched"
    assert data["enrichment_data"]["description"] == "Big Red One"
