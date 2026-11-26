"""`--max-items` bounds the entities SEARCHED, not the ones enriched (TODO gap 14).

Before, the cap tested the enriched count, so with searches that find nothing every record was
searched however small --max-items was.
"""

import json

import pytest

from src.enrichment import openserp_enrichment as OE


@pytest.fixture
def searches(monkeypatch):
    calls = []
    monkeypatch.setattr(OE, "_reset_and_check_reachable", lambda url: True)
    monkeypatch.setattr(OE, "_openserp_recently_searched", lambda d: False)
    monkeypatch.setattr(OE, "write_json_with_lock", lambda *a, **k: True)

    def search_entity(entity, f, data, name, root, search):
        OE._metric("search_attempts")  # what the real _search_entity counts
        calls.append(name)
        return False  # searched, found nothing

    monkeypatch.setattr(OE, "_search_entity", search_entity)
    return calls


def _records(d, key, n):
    d.mkdir()
    for i in range(n):
        (d / f"r{i}.json").write_text(json.dumps({key: f"Unit number {i}"}))


def test_groups_stop_after_max_items_searches(tmp_path, searches):
    _records(tmp_path / "people_groups", "group_name", 5)
    OE.enrich_groups_with_openserp(tmp_path / "people_groups", "http://x", None, 2)
    assert len(searches) == 2


def test_places_stop_after_max_items_searches(tmp_path, searches):
    _records(tmp_path / "places", "current_name", 5)
    OE.enrich_places_with_openserp(tmp_path / "places", "http://x", None, 2)
    assert len(searches) == 2


def test_a_failed_verdict_still_counts(tmp_path, monkeypatch, searches):
    """A search whose verdict failed (None: left unsearched) spent its queries: it counts."""
    calls = []

    def failed(entity, f, data, name, root, search):
        OE._metric("search_attempts")  # what the real _search_entity counts
        calls.append(name)
        return None

    monkeypatch.setattr(OE, "_search_entity", failed)
    _records(tmp_path / "places", "current_name", 5)
    OE.enrich_places_with_openserp(tmp_path / "places", "http://x", None, 2)
    assert len(calls) == 2


def test_equipment_stops_after_max_items_searches(tmp_path, monkeypatch, searches):
    from src.extraction.equipment_identity import SPECIFIC

    monkeypatch.setattr(
        "src.enrichment.equipment_images.record_scope", lambda data, grok: SPECIFIC
    )
    _records(tmp_path / "equipment", "common_name", 5)
    OE.enrich_equipment_with_openserp(tmp_path / "equipment", "http://x", None, 2)
    assert len(searches) == 2


def test_source_sections_stop_after_max_items_searches(tmp_path, searches):
    d = tmp_path / "content" / "B" / "source_section"
    d.mkdir(parents=True)
    for i in range(5):
        (d / f"s{i}.json").write_text(
            json.dumps({"SourceSectionID": f"S{i}", "operation": {"name": f"Op {i}"}})
        )
    OE.enrich_source_sections_with_openserp(tmp_path, "http://x", None, 2)
    assert len(searches) == 2


def test_no_cap_searches_all(tmp_path, searches):
    _records(tmp_path / "places", "current_name", 5)
    OE.enrich_places_with_openserp(tmp_path / "places", "http://x", None, None)
    assert len(searches) == 5


def test_places_count_eligible_and_skip_unusable_names(tmp_path, searches):
    d = tmp_path / "places"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"current_name": "Carentan"}))
    (d / "b.json").write_text(json.dumps({"current_name": "X"}))
    before = OE._metrics.get("entities_eligible", 0)
    OE.enrich_places_with_openserp(d, "http://x", None, None)
    assert searches == ["Carentan"]
    assert OE._metrics.get("entities_eligible", 0) - before == 1
