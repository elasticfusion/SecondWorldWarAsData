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


def test_p3a5_enriched_with_link_passes(tmp_path):
    rep = Report()
    _w(
        tmp_path / "people" / "p1.json",
        {
            "enrichment_status": "enriched",
            "wikipedia_url": "https://en.wikipedia.org/wiki/X",
        },
    )
    _w(
        tmp_path / "people" / "p2.json",
        {
            "enrichment_status": "enriched",
            "biographical_profile": {"grokipedia_url": "https://g/x"},
        },
    )
    check_enriched_source_linkage(tmp_path, rep)
    assert not rep.failures


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
            "enrichment_status": "enriched",
            "external_data": {"wikipedia_url": "https://en.wikipedia.org/wiki/Tiger_I"},
        },
    )
    _w(
        tmp_path / "equipment" / "e2.json",
        {
            "enrichment_status": "enriched",
            "external_data": {"additional_sources": [{"url": "https://x/y"}]},
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
