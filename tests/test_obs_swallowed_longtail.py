"""PR-E — observability swallowed-alerts LONGTAIL (Patterns 1 & 2).

Each formerly-swallowed drop must now be a COUNTED + LOUD signal: a greppable ``OBSERV
*.load_skipped dropped=N`` / ``OBSERV fetch_review.load_errors errors=N`` WARNING, with NO
control-flow change (the skip/continue/return behaviour is preserved) and fully fail-safe.
"""

import json
import logging

import pytest


def _records(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- batch_parallel: date-index builder (Pattern 1) --------------------------------------
def test_date_index_load_skipped_counts_corrupt_json(tmp_path, caplog):
    from src.extraction.batch_parallel import _build_date_id_lookup

    dates = tmp_path / "dates"
    dates.mkdir()
    # one good date record + one corrupt file
    (dates / "good.json").write_text(
        json.dumps({"date_start": "1943-07-10", "DateID": "D1"}), encoding="utf-8"
    )
    (dates / "corrupt.json").write_text("{ not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        lookup = _build_date_id_lookup(dates)

    # control-flow unchanged: the good record is still indexed, corrupt one skipped
    assert lookup == {"1943-07-10": "D1"}
    msgs = [r.getMessage() for r in _records(caplog)]
    assert any(
        "OBSERV date_index.load_skipped" in m and "dropped=1" in m for m in msgs
    ), msgs


def test_date_index_no_warning_when_all_loadable(tmp_path, caplog):
    from src.extraction.batch_parallel import _build_date_id_lookup

    dates = tmp_path / "dates"
    dates.mkdir()
    (dates / "good.json").write_text(
        json.dumps({"date_start": "1944-06-06", "DateID": "D2"}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        _build_date_id_lookup(dates)
    assert not any(
        "date_index.load_skipped" in r.getMessage() for r in _records(caplog)
    )


# --- batch_parallel: entity-index read (Pattern 1) ---------------------------------------
def test_entity_index_load_skipped_on_corrupt_record(tmp_path, caplog):
    from src.extraction import batch_parallel as bp

    entity_dir = tmp_path / "people"
    entity_dir.mkdir()
    corrupt = entity_dir / "alice.json"
    corrupt.write_text("{ broken", encoding="utf-8")

    # index already resolves the key -> existing-entity read path hits the corrupt file
    index = {"alice": "alice.json"}
    with caplog.at_level(logging.WARNING):
        entity_file, entity_id, record = bp._get_or_create_entity(
            "alice", {}, index, entity_dir, lambda o: {}, "PersonID"
        )
    # control-flow unchanged: returns empty record rather than raising
    assert record == {}
    assert entity_id == ""
    assert any(
        "OBSERV entity_index.load_skipped" in r.getMessage()
        and "dropped=1" in r.getMessage()
        for r in _records(caplog)
    )


# --- equipment: index-load skip counted (Pattern 1) --------------------------------------
def test_equipment_index_load_skipped_counted(tmp_path, caplog):
    from src.extraction.equipment import _load_json_files

    d = tmp_path / "equipment"
    d.mkdir()
    (d / "good.json").write_text(json.dumps({"common_name": "Tiger"}), encoding="utf-8")
    (d / "corrupt.json").write_text("{ nope", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        results = _load_json_files(d, skip_files=["index.json"])

    # control-flow unchanged: good file still loaded, corrupt one skipped
    assert [p.name for p, _ in results] == ["good.json"]
    msgs = [r.getMessage() for r in _records(caplog)]
    assert any(
        "OBSERV equipment.index_load_skipped" in m and "dropped=1" in m for m in msgs
    ), msgs


# --- fetch_review: empty queue due to load errors (Pattern 1) ----------------------------
def test_fetch_review_load_errors_warns_when_empty(monkeypatch, tmp_path, caplog):
    import src.enrichment.fetch_review as FR
    from src.utils import fetch_provenance as fp

    # local mode, directory with only a corrupt JSONL line -> all loads fail, queue empty
    monkeypatch.setattr(fp, "_table", lambda: None)
    d = tmp_path / "ev"
    d.mkdir()
    (d / "wayback.jsonl").write_text("{ not json\n", encoding="utf-8")
    monkeypatch.setattr(fp, "_LOCAL_DIR", d)

    with caplog.at_level(logging.WARNING):
        pending = FR.list_pending()

    # fail-closed preserved: empty queue, no raise
    assert pending == []
    assert any(
        "OBSERV fetch_review.load_errors" in r.getMessage()
        and "errors=" in r.getMessage()
        for r in _records(caplog)
    )


def test_fetch_review_no_warning_when_genuinely_empty(monkeypatch, tmp_path, caplog):
    import src.enrichment.fetch_review as FR
    from src.utils import fetch_provenance as fp

    monkeypatch.setattr(fp, "_table", lambda: None)
    d = tmp_path / "ev"
    d.mkdir()  # no files at all -> genuinely empty, zero errors
    monkeypatch.setattr(fp, "_LOCAL_DIR", d)

    with caplog.at_level(logging.WARNING):
        pending = FR.list_pending()

    assert pending == []
    assert not any(
        "fetch_review.load_errors" in r.getMessage() for r in _records(caplog)
    )


# --- logistics: items dropped counted (Pattern 2) ----------------------------------------
def test_logistics_items_dropped_counted(caplog):
    from src.extraction.logistics import _parse_logistics_response

    # one usable item + one unusable (empty description) -> 1 dropped
    response = {
        "SE1": [
            {"type": "capacity_constraint", "category": "general", "description": "x"},
            {"description": ""},
        ]
    }
    with caplog.at_level(logging.WARNING):
        result = _parse_logistics_response(response)

    # control-flow unchanged: the usable item still persisted
    assert "SE1" in result and len(result["SE1"]) == 1
    assert any(
        "OBSERV logistics.items_dropped" in r.getMessage()
        and "dropped=1" in r.getMessage()
        for r in _records(caplog)
    )


# --- source_acquisition: no-target loud (Pattern 1) --------------------------------------
def test_acquisition_no_target_no_fetcher_warns(caplog):
    from src.enrichment import source_acquisition as sa

    entry = {
        "search_status": "resolved",
        "search_source": "definitely_unregistered_source_xyz",
        "BibliographyID": "B9",
    }
    with caplog.at_level(logging.WARNING):
        plan = sa.plan_acquisition(entry)

    # control-flow unchanged: still returns None for an unacquirable entry
    assert plan is None
    assert any(
        "OBSERV acquisition.no_target" in r.getMessage()
        and "no_fetcher" in r.getMessage()
        for r in _records(caplog)
    )
