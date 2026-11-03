"""PR3 guard: OpenSERP equipment + source_section enrichment must gate the enriched COUNT (and the
`entities_enriched` metric) on the persisted write succeeding. On origin/main the count/_metric was
incremented BEFORE an unchecked `write_json_with_lock`, so a failed persist read as a successful
enrichment (the enrichment was silently lost but reported as applied). These tests fail on
origin/main and pass on the fix.
"""

from pathlib import Path

import src.enrichment.openserp_enrichment as oe


def _reset():
    oe.reset_metrics()


def test_equipment_write_failure_not_counted(monkeypatch, tmp_path):
    _reset()
    f = tmp_path / "eq1.json"
    f.write_text('{"common_name": "Tiger I"}', encoding="utf-8")

    # enrichment "changed" the record, but the persisted write FAILS.
    monkeypatch.setattr(oe, "enrich_one_equipment", lambda *a, **k: True)
    monkeypatch.setattr(oe, "_openserp_recently_searched", lambda d: False)
    monkeypatch.setattr(oe, "_usable_entity_name", lambda n: True)
    monkeypatch.setattr(oe, "write_json_with_lock", lambda *a, **k: False)

    result = oe._process_equipment_file(Path(f), "http://x", None)
    assert result is False  # a failed persist is NOT a counted enrichment
    m = oe.get_metrics()
    assert m["entities_enriched"] == 0  # not counted
    assert m["write_failed"] >= 1  # the loss is surfaced


def test_equipment_write_success_counted(monkeypatch, tmp_path):
    _reset()
    f = tmp_path / "eq2.json"
    f.write_text('{"common_name": "Panther"}', encoding="utf-8")
    monkeypatch.setattr(oe, "enrich_one_equipment", lambda *a, **k: True)
    monkeypatch.setattr(oe, "_openserp_recently_searched", lambda d: False)
    monkeypatch.setattr(oe, "_usable_entity_name", lambda n: True)
    monkeypatch.setattr(oe, "write_json_with_lock", lambda *a, **k: True)

    result = oe._process_equipment_file(Path(f), "http://x", None)
    assert result is True
    assert oe.get_metrics()["entities_enriched"] == 1


def test_source_section_write_failure_not_counted(monkeypatch, tmp_path):
    _reset()
    data = {"operation": {"name": "Cobra"}, "primary_sources": []}
    f = tmp_path / "ss1.json"
    f.write_text("{}", encoding="utf-8")

    def _enrich(d, *a, **k):
        d["primary_sources"] = [{"url": "http://s"}]  # a change happened
        return True

    monkeypatch.setattr(oe, "enrich_one_source_section", _enrich)
    monkeypatch.setattr(oe, "_openserp_recently_searched", lambda d: False)
    monkeypatch.setattr(oe, "write_json_with_lock", lambda *a, **k: False)

    result = oe._process_one_source_section(Path(f), data, "http://x", None)
    assert result is False  # failed persist not counted as enriched
    m = oe.get_metrics()
    assert m["entities_enriched"] == 0
    assert m["write_failed"] >= 1


def test_source_section_write_success_counted(monkeypatch, tmp_path):
    _reset()
    data = {"operation": {"name": "Market Garden"}, "primary_sources": []}
    f = tmp_path / "ss2.json"
    f.write_text("{}", encoding="utf-8")

    def _enrich(d, *a, **k):
        d["primary_sources"] = [{"url": "http://s"}]
        return True

    monkeypatch.setattr(oe, "enrich_one_source_section", _enrich)
    monkeypatch.setattr(oe, "_openserp_recently_searched", lambda d: False)
    monkeypatch.setattr(oe, "write_json_with_lock", lambda *a, **k: True)

    result = oe._process_one_source_section(Path(f), data, "http://x", None)
    assert result is True
    assert oe.get_metrics()["entities_enriched"] == 1
