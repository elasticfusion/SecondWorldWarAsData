"""Task 1.2f PART 2 (owner 2026-11-18): "If data corruption is the cause, error out loudly". Every
Phase 3 record loader goes through entity_records.load_record: a corrupt stored record is
alerted (record.corrupt), skipped so the step goes on with the rest, and Phase 3 then exits
EXIT_DATA_CORRUPT so the book is held for the operator. A missing file is not corruption.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("S3_BUCKET", "test-bucket")

from src.utils import entity_records, stop_cause  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_ledger(monkeypatch):
    entity_records.reset_corrupt_records()
    alerts = []
    import src.utils.observe as obs

    monkeypatch.setattr(
        obs, "observe_error", lambda s, e, **k: alerts.append((s, e, k))
    )
    yield alerts
    entity_records.reset_corrupt_records()


def _corrupt(tmp_path, entity="places", name="bad.json", body="{not json"):
    d = tmp_path / entity
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(body, encoding="utf-8")
    return f


@pytest.mark.parametrize("body", ["{not json", "[1, 2]", "\udcff"])
def test_a_corrupt_record_is_alerted_remembered_and_skipped(
    tmp_path, _fresh_ledger, body
):
    f = _corrupt(tmp_path)
    if body == "\udcff":
        f.write_bytes(b"\xff\xfe\x00")
    assert entity_records.load_record(f, "geocode") is None
    assert entity_records.corrupt_records() == [str(f)]
    [(scope, event, fields)] = _fresh_ledger
    assert (scope, event) == ("record", "corrupt")
    assert fields["file"] == "bad.json" and fields["entity"] == "places"
    assert "held" in fields["note"] and fields["step"] == "geocode"


def test_a_missing_record_is_not_corruption_or_a_warning(
    tmp_path, _fresh_ledger, caplog
):
    """Dedup merges files away mid-run: that is expected, not an alert."""
    assert entity_records.load_record(tmp_path / "gone.json", "geocode") is None
    assert entity_records.corrupt_records() == [] and _fresh_ledger == []
    assert "OBSERV record.unreadable" not in caplog.text


def test_an_unreadable_disk_file_warns_but_is_not_corruption(tmp_path, caplog):
    d = _corrupt(tmp_path, name="dir.json")
    d.unlink()
    d.mkdir()  # reading a directory raises IsADirectoryError (an OSError)
    assert entity_records.load_record(d, "geocode") is None
    assert entity_records.corrupt_records() == []
    assert "OBSERV record.unreadable" in caplog.text


def test_a_good_record_loads(tmp_path):
    f = _corrupt(tmp_path, body='{"PlaceID": "p1"}')
    assert entity_records.load_record(f, "geocode") == {"PlaceID": "p1"}
    assert entity_records.corrupt_records() == []


# --- every Phase 3 loader uses it --------------------------------------------------------------


def _places_geo(f):
    from src.enrichment import places_geo

    return places_geo._load_place(f)


def _noaa(f):
    from src.enrichment import noaa_weather

    return noaa_weather._load_enrichable_weather(f)


def _openserp_iter(f):
    from src.enrichment import openserp_enrichment as oe

    return list(oe._iter_loadable_files([f])) or None


def _openserp_people(f):
    from src.enrichment import openserp_enrichment as oe

    return oe._collect_people_candidates(f.parent, "http://x", None) or None


def _openserp_equipment(f):
    from src.enrichment import openserp_enrichment as oe

    return oe._load_equipment_record(f)


def _groups_wikipedia(f):
    from src.enrichment import groups_wikipedia

    return groups_wikipedia._enrich_one_group_file(f) != (False, False) or None


def _equipment_wikipedia(f):
    from src.enrichment import equipment_wikipedia

    return equipment_wikipedia._enrich_one_equipment_file(f) != (False, False) or None


def _equipment_images(f):
    from src.enrichment import equipment_images

    counts = {"unreadable": 0}
    out = equipment_images._load_record(f, counts)
    assert counts["unreadable"] == 1
    return out


def _geocode(f):
    from src.enrichment import places_grok_geocode as pg

    report = SimpleNamespace(unreadable=0)
    pg._geocode_one_file(f, None, False, False, report, None)
    assert report.unreadable == 1
    return None


def _bibliography(f):
    from src.enrichment import bibliography_resolver as br

    return br._process_bib_file(f, None, {}, {})


def _parsed_docs(f):
    import phase3_enrich_data as p3

    return p3._load_parsed_docs(f.parents[3]) or None


LOADERS = {
    "places_geo": (_places_geo, "places"),
    "noaa_weather": (_noaa, "weather"),
    "openserp": (_openserp_iter, "people"),
    "openserp_people": (_openserp_people, "people"),
    "openserp_equipment": (_openserp_equipment, "equipment"),
    "groups_wikipedia": (_groups_wikipedia, "people_groups"),
    "equipment_wikipedia": (_equipment_wikipedia, "equipment"),
    "equipment_images": (_equipment_images, "equipment"),
    "geocode": (_geocode, "places"),
    "bibliography_resolve": (_bibliography, "bibliography"),
}


@pytest.mark.parametrize("step", sorted(LOADERS))
def test_every_phase3_loader_holds_the_book_on_a_corrupt_record(tmp_path, step):
    loader, entity = LOADERS[step]
    f = _corrupt(tmp_path, entity=entity)
    assert loader(f) is None
    assert entity_records.corrupt_records() == [str(f)], step


def test_a_corrupt_parsed_doc_holds_the_book(tmp_path):
    f = _corrupt(tmp_path, entity="content/Utah/chapter1", name="ch1-parsed.json")
    _parsed_docs(f)
    assert entity_records.corrupt_records() == [str(f)]


# --- Phase 3's exit and the alert --------------------------------------------------------------


def test_phase3_exits_data_corrupt_when_a_record_was_skipped(tmp_path):
    import phase3_enrich_data as p3

    assert p3._exit_for_steps({"places": {"status": "ok"}}) == 0
    entity_records.load_record(_corrupt(tmp_path), "geocode")
    assert p3._exit_for_steps({"places": {"status": "ok"}}) == (
        stop_cause.EXIT_DATA_CORRUPT
    )
    # corruption wins over an ordinary step failure: a retry cannot repair the record
    assert p3._exit_for_steps({"a": {"status": "error", "corrupt": False}}) == (
        stop_cause.EXIT_DATA_CORRUPT
    )
    assert p3._corrupt_record_names() == ["places/bad.json"]


def test_the_hold_alert_names_the_corrupt_records(monkeypatch):
    import ecs_entrypoint as ee

    monkeypatch.setattr(
        ee,
        "_phase_results",
        lambda: {
            "source_stats": {"places": {"status": "ok"}},
            "corrupt_records": ["places/bad.json", "weather/w1.json"],
        },
    )
    sent = []
    monkeypatch.setattr(ee, "observe_error", lambda s, e, **k: sent.append(k))
    reason = ee._alert_data_corruption()
    [fields] = sent
    assert "places/bad.json" in fields["error"] and "weather/w1.json" in fields["error"]
    assert fields["steps"] == "skipped records" and "skipped records" in reason


@pytest.mark.parametrize(
    "name,body",
    [("review_queue.json", "[{\"item\": 1}]"),  # a list, written from book text (recall_gap)
     (".processed_events.json", "{trunc"),  # a regenerable state file, truncated
     ("index.json", "{"), ("not_people.json", "[]"), ("x.json.lock", ""),
     ("coords.json", "{trunc")],  # places/coords.json, written non-atomically by the wrapper
)  # fmt: skip
def test_a_sidecar_is_never_a_corrupt_record(tmp_path, _fresh_ledger, name, body):
    """Only records can hold a book: indexes, reports, curated lists and state files cannot."""
    f = _corrupt(tmp_path, entity="people", name=name, body=body)
    assert entity_records.load_record(f, "openserp_people") is None
    assert entity_records.corrupt_records() == [] and _fresh_ledger == []


def test_a_file_read_by_several_steps_is_alerted_and_listed_once(
    tmp_path, _fresh_ledger
):
    f = _corrupt(tmp_path)
    for step in ("places_geo", "geocode", "parsed_docs"):
        entity_records.load_record(f, step)
    assert entity_records.corrupt_records() == [str(f)] and len(_fresh_ledger) == 1


def test_a_too_deeply_nested_record_is_corrupt(tmp_path):
    f = _corrupt(tmp_path, body="[" * 200000 + "]" * 200000)
    assert entity_records.load_record(f, "geocode", expect=list) is None
    assert entity_records.corrupt_records() == [str(f)]


def test_the_step_goes_on_past_a_corrupt_record(tmp_path):
    from src.enrichment import openserp_enrichment as oe

    bad = _corrupt(tmp_path, entity="people", name="a.json")
    good = _corrupt(tmp_path, entity="people", name="b.json", body='{"name": "Patton"}')
    assert [f for f, _ in oe._iter_loadable_files([bad, good])] == [good]
    assert entity_records.corrupt_records() == [str(bad)]


def test_a_long_list_says_it_is_cut(tmp_path):
    import phase3_enrich_data as p3

    for n in range(3):
        entity_records.load_record(_corrupt(tmp_path, name=f"b{n}.json"), "geocode")
    assert p3._corrupt_record_names(limit=2) == [
        "places/b0.json",
        "places/b1.json",
        "... and 1 more",
    ]


def test_a_non_equipment_record_is_skipped_but_not_counted_unreadable(tmp_path):
    from src.enrichment import equipment_images

    f = _corrupt(tmp_path, entity="equipment", body='{"name": "not equipment"}')
    counts = {"unreadable": 0}
    assert equipment_images._load_record(f, counts) is None
    assert counts["unreadable"] == 0 and entity_records.corrupt_records() == []


def test_a_dedup_ref_rebase_writes_atomically(tmp_path, monkeypatch):
    """A kill mid-write must not leave a truncated record (Phase 3 would hold the book)."""
    from src.dedup import merge
    import src.utils.file_lock as fl

    f = tmp_path / "event.json"
    f.write_text(
        json.dumps({"Event": {"Sub-events": [{"people": ["old"]}]}}), encoding="utf-8"
    )
    writes = []
    real = fl._atomic_write_json
    monkeypatch.setattr(
        fl, "_atomic_write_json", lambda p, d: writes.append(p) or real(p, d)
    )
    fn = merge._rebase_event_file
    fn(f, "old", "new", "people")
    assert writes == [f]
    assert "new" in f.read_text(encoding="utf-8")
