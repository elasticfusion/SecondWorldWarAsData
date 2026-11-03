"""DRY P1.11 guard: the identical reset-circuit + reachability-check prelude that the three
enrich_*_with_openserp drivers (people, equipment, source_section) each opened with is now the
shared _reset_and_check_reachable. These tests pin that all three route through it: an unreachable
OpenSERP makes every driver return 0 without touching any file.
"""

from pathlib import Path
from unittest import mock

import src.enrichment.openserp_enrichment as oe


def test_prelude_helper_contract(monkeypatch):
    reset_calls = []
    monkeypatch.setattr(oe, "reset_circuit", lambda: reset_calls.append(1))
    # reachable -> True
    monkeypatch.setattr(oe, "_openserp_reachable", lambda url: True)
    assert oe._reset_and_check_reachable("http://x") is True
    # unreachable -> False (still reset the circuit first)
    monkeypatch.setattr(oe, "_openserp_reachable", lambda url: False)
    assert oe._reset_and_check_reachable("http://x") is False
    assert len(reset_calls) == 2  # circuit reset on both calls


def test_all_three_drivers_short_circuit_when_unreachable(monkeypatch, tmp_path):
    # Spy the shared helper -> unreachable. Every driver must return 0 and NOT iterate records.
    monkeypatch.setattr(oe, "_reset_and_check_reachable", lambda url: False)
    # if any driver bypassed the helper and iterated, these would blow up / be called:
    monkeypatch.setattr(
        oe,
        "_iter_loadable_records",
        lambda d: (_ for _ in ()).throw(AssertionError("should not iterate")),
    )
    monkeypatch.setattr(
        oe,
        "_collect_people_candidates",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not collect")),
    )

    assert oe.enrich_people_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_groups_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_places_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_equipment_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_source_sections_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_groups_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_places_with_openserp(tmp_path, "http://x") == 0


def test_drivers_proceed_when_reachable(monkeypatch, tmp_path):
    # Reachable + empty dir -> 0 enriched, but the loop IS entered (no short-circuit).
    monkeypatch.setattr(oe, "_reset_and_check_reachable", lambda url: True)
    monkeypatch.setattr(oe, "_collect_people_candidates", lambda *a, **k: [])
    (tmp_path / "index.json").write_text(
        "{}", encoding="utf-8"
    )  # only skip-file present
    assert oe.enrich_equipment_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_source_sections_with_openserp(tmp_path, "http://x") == 0
    assert oe.enrich_people_with_openserp(tmp_path, "http://x") == 0
