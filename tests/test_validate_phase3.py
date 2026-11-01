"""Regression tests for scripts/validate_phase3.py (post-Phase-3 validator)."""

import json
from pathlib import Path

from scripts.validate_phase3 import (
    check_coverage_floors,
    check_enriched_source_linkage,
    run,
)
from scripts.validate_phase2 import Report


def _w(p: Path, body: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body))


def test_p3a5_enriched_without_link_is_hard_fail(tmp_path):
    rep = Report()
    _w(tmp_path / "people" / "p1.json", {"enrichment_status": "enriched", "name": "X"})
    check_enriched_source_linkage(tmp_path, rep)
    assert any("P3-A5" in f for f in rep.failures)


# A fully-compliant enriched person: schema-valid (PersonID+name, link in the schema-legal
# biographical_profile, not top-level), version-matched, and traceable (event_mention w/ EventID).
def _compliant_person(pid="01HX7YZABCDEFGHJKMNPQRSTVW", link_key="wikipedia_url"):
    return {
        "PersonID": pid,
        "name": "Courtney Hodges",
        "_schema_version": "2.27",
        "enrichment_status": "enriched",
        "biographical_profile": {
            link_key: "https://en.wikipedia.org/wiki/Courtney_Hodges"
        },
        "event_mentions": [
            {"EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH", "original_text": "x"}
        ],
    }


def test_p3a5_fully_compliant_enriched_passes(tmp_path):
    rep = Report()
    _w(tmp_path / "people" / "p1.json", _compliant_person())
    _w(
        tmp_path / "people" / "p2.json",
        _compliant_person(pid="01HX7YZABCDEFGHJKMNPQRSTVX", link_key="grokipedia_url"),
    )
    check_enriched_source_linkage(tmp_path, rep)
    assert not rep.failures, rep.failures


def test_p3a5_enriched_schema_invalid_fails(tmp_path):
    """Enriched but missing a mandatory field (PersonID) -> P3-A5 schema failure (new rigor)."""
    rep = Report()
    rec = _compliant_person()
    del rec["PersonID"]
    _w(tmp_path / "people" / "p1.json", rec)
    check_enriched_source_linkage(tmp_path, rep)
    assert any(
        "schema-invalid" in f and "PersonID" in f for f in rep.failures
    ), rep.failures


def test_p3a5_enriched_untraceable_fails(tmp_path):
    """Enriched but no event_mention EventID -> not traceable to source -> P3-A5 failure."""
    rep = Report()
    rec = _compliant_person()
    rec["event_mentions"] = []
    _w(tmp_path / "people" / "p1.json", rec)
    check_enriched_source_linkage(tmp_path, rep)
    assert any("not traceable to source" in f for f in rep.failures), rep.failures


def test_p3a5_enriched_version_mismatch_fails(tmp_path):
    rep = Report()
    rec = _compliant_person()
    rec["_schema_version"] = "1.99"
    _w(tmp_path / "people" / "p1.json", rec)
    check_enriched_source_linkage(tmp_path, rep)
    assert any("_schema_version" in f for f in rep.failures), rep.failures


def test_p3a5_ignores_unenriched(tmp_path):
    rep = Report()
    _w(tmp_path / "people" / "p1.json", {"enrichment_status": "not_found", "name": "X"})
    _w(tmp_path / "places" / "pl1.json", {"name": "Y"})  # no status -> not checked
    check_enriched_source_linkage(tmp_path, rep)
    assert not rep.failures


def test_p3b4_zero_geocoded_is_warn_by_default(tmp_path):
    rep = Report()
    _w(tmp_path / "places" / "pl1.json", {"name": "Aachen"})  # no coords
    check_coverage_floors(tmp_path, rep, strict=False)
    assert any("P3-B4" in w and "coordinates" in w for w in rep.warnings)
    assert not rep.failures


def test_p3b4_geocoded_place_no_warn(tmp_path):
    rep = Report()
    _w(
        tmp_path / "places" / "pl1.json",
        {"name": "Aachen", "location": {"latitude": 50.8, "longitude": 6.1}},
    )
    check_coverage_floors(tmp_path, rep, strict=False)
    assert not any("coordinates" in w for w in rep.warnings)


def test_p3b4_strict_promotes_to_fail(tmp_path):
    rep = Report()
    _w(tmp_path / "places" / "pl1.json", {"name": "Aachen"})
    check_coverage_floors(tmp_path, rep, strict=True)
    assert any("P3-B4" in f for f in rep.failures)


def test_run_missing_root_returns_1(tmp_path):
    assert run(str(tmp_path / "does_not_exist"), strict_coverage=False) == 1


def test_p3a5_equipment_link_in_external_data_passes(tmp_path):
    """Equipment stores its source link ONLY under external_data (schema forbids top-level _url).
    A5 must recognize it there (regression for the review-found false-fail)."""
    rep = Report()
    _w(
        tmp_path / "equipment" / "e1.json",
        {
            "EquipmentID": "01HX7YZABCDEFGHJKMNPQRSTVW",
            "_schema_version": "2.26",
            "enrichment_status": "enriched",
            "external_data": {"wikipedia_url": "https://en.wikipedia.org/wiki/Tiger_I"},
            "event_mentions": [{"EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH"}],
        },
    )
    _w(
        tmp_path / "equipment" / "e2.json",
        {
            "EquipmentID": "01HX7YZABCDEFGHJKMNPQRSTVX",
            "_schema_version": "2.26",
            "enrichment_status": "enriched",
            "external_data": {"additional_sources": [{"url": "https://x/y"}]},
            "event_mentions": [{"EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH"}],
        },
    )
    check_enriched_source_linkage(tmp_path, rep)
    assert not rep.failures, rep.failures


def test_p3a5_equipment_enriched_no_link_anywhere_fails(tmp_path):
    rep = Report()
    _w(
        tmp_path / "equipment" / "e1.json",
        {"enrichment_status": "enriched", "external_data": {}},
    )
    check_enriched_source_linkage(tmp_path, rep)
    assert any("P3-A5" in f for f in rep.failures)


def test_p3a5_places_not_gated(tmp_path):
    """Phase-3 place enrichment (hierarchy/historical_names) is link-free by design — an enriched,
    linkless place must NOT be gated by A5 (regression for the review-found false-fail).
    """
    rep = Report()
    _w(
        tmp_path / "places" / "pl1.json",
        {
            "enrichment_status": "enriched",
            "hierarchy": {"country": "Germany"},
            "historical_names": [],
        },
    )
    check_enriched_source_linkage(tmp_path, rep)
    assert not rep.failures, rep.failures


def test_p3a5_bibliography_not_gated(tmp_path):
    """Bibliography has no enrichment_status (uses search_status) -> never gated by A5."""
    rep = Report()
    _w(
        tmp_path / "bibliography" / "bib_1.json",
        {"search_status": "resolved", "title": "T"},
    )
    check_enriched_source_linkage(tmp_path, rep)
    assert not rep.failures


def test_p3a5_group_structured_facts_source_is_attribution(tmp_path):
    """A group enriched via structured Grok facts records provenance under enrichment_data._source
    (not a top-level URL) — that IS valid attribution, so P3-A5 must NOT flag it."""
    rep = Report()
    _w(
        tmp_path / "people_groups" / "g1.json",
        {
            "GroupID": "01HX7YZABCDEFGHJKMNPQRSTVW",
            "_schema_version": "2.29",
            "enrichment_status": "enriched",
            "enrichment_data": {
                "_source": {
                    "type": "grok",
                    "query": "28th Division",
                    "retrieved_at": "2026-10-25",
                }
            },
            "event_mentions": [{"EventID": "1HZMC6KKXZEVVB3Q0BEBKDTDNH"}],
        },
    )
    check_enriched_source_linkage(tmp_path, rep)
    assert not rep.failures, rep.failures


def test_a1a2_list_sidecar_does_not_crash(tmp_path):
    """A list-shaped file in an entity dir must not crash check_schema_and_version."""
    from scripts.validate_phase2 import check_schema_and_version

    _w(tmp_path / "bibliography" / "review_queue.json", [])  # skipped by _SKIP_NAMES
    (tmp_path / "bibliography" / "weird_list.json").write_text(
        "[1,2,3]"
    )  # not skipped -> clean fail
    rep = Report()
    check_schema_and_version(tmp_path, rep)  # must not raise
    assert any("not a JSON object" in f for f in rep.failures)


def test_a3_a4_list_record_does_not_crash(tmp_path):
    """A list-shaped file in a mention/people dir must not crash A3 (traceback) or A4 (enrichment)."""
    from scripts.validate_phase2 import check_source_traceback, check_people_enrichment

    (tmp_path / "people").mkdir(parents=True, exist_ok=True)
    (tmp_path / "people" / "weird_list.json").write_text("[1,2,3]")
    rep = Report()
    check_source_traceback(tmp_path, rep)  # must not raise
    check_people_enrichment(tmp_path, rep, strict=False)  # must not raise
    assert any("A3" in f and "not a JSON object" in f for f in rep.failures)
