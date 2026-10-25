"""Regression for Phase-3 review MEDs: M3 (historical_names KeyError on malformed existing entries)
+ H4 (P3-B4 bibliography coverage marker was always-falsy top-level 'isbn')."""

import src.extraction.enrich_places as P
from scripts.validate_phase3 import check_coverage_floors
from scripts.validate_phase2 import Report
import json
from pathlib import Path


# --- M3: malformed existing historical_names must not crash (place silently un-enriched) ----
def test_m3_malformed_existing_historical_names_no_crash():
    data = {
        "historical_names": [
            "not-a-dict",
            {"no_name_key": 1},
            {"name": "Aix-la-Chapelle"},
        ]
    }
    enrichment = {"historical_names": [{"name": "Aachen", "language": "German"}]}
    # pre-fix: `{h["name"] for h in ...}` -> TypeError/KeyError on the bad entries
    changed = P._merge_historical_names(data, enrichment)
    assert changed is True
    assert any(
        isinstance(h, dict) and h.get("name") == "Aachen"
        for h in data["historical_names"]
    )


def test_m3_dedups_against_valid_existing():
    data = {"historical_names": [{"name": "Aachen"}]}
    enrichment = {"historical_names": [{"name": "Aachen"}]}  # dup -> no change
    assert P._merge_historical_names(data, enrichment) is False


# --- H4: bibliography coverage uses search_status/resource_urls, not a top-level isbn -------
def _w(p: Path, body: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body))


def test_h4_resolved_bibliography_no_false_warn(tmp_path):
    # A resolved bib record (isbn nested under citation) must count as covered -> no warn.
    _w(
        tmp_path / "bibliography" / "b1.json",
        {"search_status": "resolved", "citation": {"isbn": "x"}},
    )
    rep = Report()
    check_coverage_floors(tmp_path, rep, strict=False)
    assert not any("bibliography" in w for w in rep.warnings), rep.warnings


def test_h4_resource_urls_counts_as_resolved(tmp_path):
    _w(
        tmp_path / "bibliography" / "b1.json",
        {"resource_urls": ["https://archive.org/x"]},
    )
    rep = Report()
    check_coverage_floors(tmp_path, rep, strict=False)
    assert not any("bibliography" in w for w in rep.warnings)


def test_h4_unresolved_bibliography_still_warns(tmp_path):
    _w(tmp_path / "bibliography" / "b1.json", {"search_status": "not_found"})
    rep = Report()
    check_coverage_floors(tmp_path, rep, strict=False)
    assert any("bibliography" in w and "resolved" in w for w in rep.warnings)
