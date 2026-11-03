"""PR1 (M2/M3): orchestration calls that were outside run_step must not abort the phase.
_run_openserp_enrichment's scaffolding (import/reset/metrics) is now fail-safe."""

from __future__ import annotations

import sys
import types

import phase3_enrich_data as p3


def test_openserp_import_failure_does_not_abort_phase(monkeypatch, caplog):
    """If the openserp_enrichment import/reset raises, _run_openserp_enrichment returns 0 + a loud
    OBSERV, instead of propagating and aborting the rest of Phase 3."""
    import logging

    # Force the import inside _run_openserp_enrichment to raise.
    broken = types.ModuleType("src.enrichment.openserp_enrichment")

    def _raise_on_attr(name):
        raise ImportError("openserp module broken")

    broken.__getattr__ = _raise_on_attr  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.enrichment.openserp_enrichment", broken)

    class _Args:
        output_dir = __import__("pathlib").Path("/tmp")
        max_items = 1
        people_only = False

    def _run_step(name, fn):
        return 0

    with caplog.at_level(logging.WARNING):
        out = p3._run_openserp_enrichment(
            _Args(), {"external_maps": {}}, object(), _run_step
        )

    assert out == 0  # did not raise -> phase continues
    assert any("OBSERV phase3.openserp_skipped" in r.message for r in caplog.records)
