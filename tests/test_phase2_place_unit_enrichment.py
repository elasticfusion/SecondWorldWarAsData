"""Phase 2's batch path looks places and units up on Grokipedia/Wikipedia (since 2026-10-06 the
lookups lived in per-type extractors the batch path never calls); a unit's page counts only when
it places the unit in WWII (deterministic text rule, no Grok request); and equipment found in two
parallel chapters is one record."""

import json
from pathlib import Path
from unittest import mock

import pytest

from src.extraction import batch_parallel as bp


@pytest.fixture(autouse=True)
def _lookups_on(monkeypatch):
    monkeypatch.setenv("PHASE2_REFERENCE_LOOKUPS", "1")  # every lookup below is mocked


def _record(tmp_path, entity, data):
    d = tmp_path / entity
    d.mkdir(exist_ok=True)
    f = d / "x.json"
    f.write_text(json.dumps(data))
    return f


def test_a_place_written_by_the_batch_path_is_looked_up_once(tmp_path):
    f = _record(
        tmp_path,
        "places",
        {"PlaceID": "01HZZZZZZZZZZZZZZZZZZZZZP1", "current_name": "Cherbourg"},
    )

    def grok(place):
        place["grokipedia_checked_at"] = "2026-11-14"
        place["grokipedia_url"] = "https://grokipedia.com/page/Cherbourg"

    def wiki(place):
        place["wikipedia_checked_at"] = "2026-11-14"

    with (
        mock.patch(
            "src.extraction.enrich_places.enrich_place_from_grokipedia",
            side_effect=grok,
        ),
        mock.patch(
            "src.extraction.enrich_places.enrich_place_from_wikipedia", side_effect=wiki
        ),
    ):
        bp._enrich_place_record(f)
    saved = json.loads(f.read_text())
    assert (
        saved["grokipedia_url"].endswith("Cherbourg") and saved["wikipedia_checked_at"]
    )


def test_a_unit_written_by_the_batch_path_is_looked_up(tmp_path):
    f = _record(
        tmp_path,
        "people_groups",
        {
            "GroupID": "01HZZZZZZZZZZZZZZZZZZZZZG1",
            "group_name": "1st Infantry Division",
        },
    )

    def wiki(group):
        group["wikipedia_checked_at"] = "2026-11-14"
        group["wikipedia_url"] = "https://en.wikipedia.org/wiki/1st_Infantry_Division"

    with mock.patch(
        "src.enrichment.groups_wikipedia.enrich_group_from_wikipedia", side_effect=wiki
    ):
        bp._enrich_group_record(f)
    assert json.loads(f.read_text())["wikipedia_url"].endswith("Division")


def test_a_failed_lookup_never_fails_extraction(tmp_path, caplog):
    f = _record(
        tmp_path,
        "people_groups",
        {"GroupID": "01HZZZZZZZZZZZZZZZZZZZZZG1", "group_name": "X Corps"},
    )
    before = f.read_text()
    with mock.patch(
        "src.enrichment.groups_wikipedia.enrich_group_from_wikipedia",
        side_effect=RuntimeError("boom"),
    ):
        bp._enrich_group_record(f)
    assert f.read_text() == before
    assert "enrich_skipped" in caplog.text


def test_the_batch_path_wires_the_hooks():
    import inspect

    src = inspect.getsource(bp._extract_places) + inspect.getsource(
        bp._extract_people_groups
    )
    assert "_enrich_place_record" in src and "_enrich_group_record" in src


# --- units: WWII pages only ---------------------------------------------------------------------


def test_a_unit_page_must_place_it_in_wwii():
    from src.enrichment import groups_wikipedia as G

    wwii = {
        "title": "1st Infantry Division",
        "extract": "A division of the US Army in World War II.",
    }
    modern = {
        "title": "1st Division",
        "extract": "A modern armored division formed in 2004.",
    }
    with mock.patch.object(G, "_fetch_license", return_value=None):
        assert G._extract_group_page(wwii, "1st Infantry Division")
        assert G._extract_group_page(modern, "1st Division") is None


def test_a_cached_unit_page_from_before_the_rule_is_held_to_it():
    from src.enrichment import groups_wikipedia as G

    cached = {"extract": "A modern armored division.", "wikipedia_url": "u"}
    with mock.patch(
        "src.enrichment._wikipedia_common.cached_variant_search", return_value=cached
    ):
        assert G.search_group_wikipedia("1st Division") is None


# --- equipment: one record across parallel chapters ---------------------------------------------


def test_equipment_created_by_one_chapter_is_found_by_another(tmp_path, monkeypatch):
    from src.extraction import equipment as eq

    monkeypatch.setattr(eq, "_CREATED_THIS_RUN", {})
    monkeypatch.setattr(eq, "_resolve_named_vessel", lambda *a, **k: None)
    created = []

    def create(data, mention, out_dir, index, client, enrich):
        path = out_dir / f"{data['common_name']}.json"
        path.write_text("{}")
        index[data["common_name"]] = path
        created.append(path)
        return path

    merged = []
    monkeypatch.setattr(eq, "_create_new_equipment", create)
    monkeypatch.setattr(
        eq, "_merge_into_existing", lambda path, *a, **k: merged.append(path) or path
    )
    chapter_a, chapter_b = {}, {}  # each chapter's own index snapshot
    eq.merge_or_create_equipment({"common_name": "M4 Sherman"}, {}, tmp_path, chapter_a)
    eq.merge_or_create_equipment({"common_name": "M4 Sherman"}, {}, tmp_path, chapter_b)
    assert len(created) == 1 and merged == created
