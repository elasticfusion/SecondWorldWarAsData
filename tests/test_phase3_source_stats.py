"""C3: Phase 3 completion notification surfaces per-source enrichment stats +
previously-swallowed failures (so they reach the operator via email + Slack)."""

import json
import os
from pathlib import Path

os.environ.setdefault("S3_BUCKET", "test-bucket")

import ecs_entrypoint as ee  # noqa: E402


def _write_results(tmp: Path, payload: dict) -> None:
    out = tmp / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / ".phase_results.json").write_text(json.dumps(payload), encoding="utf-8")


def test_results_section_lists_per_source_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    _write_results(
        tmp_path,
        {
            "enriched": 42,
            "entity_counts": {"people": 10, "places": 20},
            "source_stats": {
                "people": {"enriched": 10, "status": "ok"},
                "geocode": {
                    "enriched": 20,
                    "status": "ok",
                    "attempted": 25,
                    "not_found": 3,
                    "errors": 2,
                },
            },
            "errored_sources": [],
        },
    )
    section = ee._build_results_section()
    assert "Enrichment by source:" in section
    assert "✓ people: 10" in section
    assert "attempted 25" in section and "not_found 3" in section


def test_results_section_surfaces_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    _write_results(
        tmp_path,
        {
            "enriched": 5,
            "entity_counts": {"people": 5},
            "source_stats": {
                "people": {"enriched": 5, "status": "ok"},
                "openserp_people": {
                    "enriched": 0,
                    "status": "error",
                    "error": "connection refused",
                },
            },
            "errored_sources": ["openserp_people"],
        },
    )
    section = ee._build_results_section()
    # The failed source is shown AND escalated in a prominent warning block.
    assert "✗ openserp_people: ERROR" in section
    assert "connection refused" in section
    assert "ENRICHMENT FAILURES" in section
    assert "openserp_people" in section


def test_results_section_clean_run_no_failure_block(tmp_path, monkeypatch):
    monkeypatch.setattr(ee, "WORKDIR", tmp_path)
    _write_results(
        tmp_path,
        {
            "enriched": 5,
            "entity_counts": {"people": 5},
            "source_stats": {"people": {"enriched": 5, "status": "ok"}},
            "errored_sources": [],
        },
    )
    section = ee._build_results_section()
    assert "ENRICHMENT FAILURES" not in section
    assert "✓ people: 5" in section
