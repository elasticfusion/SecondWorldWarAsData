"""Task 1.10: every Phase 3 write refreshes the record's metadata (``_schema_version`` at the
entity's own version, ``_last_updated``), so incremental change detection sees the change. Both
shared writers stamp: ``write_json_with_lock`` (by ``entity=`` or the record's directory) and
``locked_json``'s save; Phase 3 has no raw write to an entity record (the gate's raw-write ratchet
keeps it so)."""

import datetime as dt
import json

import pytest

from src.schemas import entity_version
from src.utils.file_lock import locked_json, write_json_with_lock

OLD = "2000-01-01"


def _place(pid, name, **extra):
    rec = {"PlaceID": pid, "current_name": name, "geography_type": "town",
           "historical_names": [], "aliases": [], "event_mentions": [],
           "_schema_version": "2.24", "_last_updated": OLD}  # fmt: skip
    rec.update(extra)
    return rec


def test_linking_a_parent_place_refreshes_the_record(tmp_path):
    """The step the TODO named (places.link_parent_place_ids wrote without entity=)."""
    from src.extraction.places import link_parent_place_ids

    places = tmp_path / "places"
    places.mkdir()
    country = _place("01HZZZZZZZZZZZZZZZZZZZZZZA", "Belgium", geography_type="country")
    town = _place(
        "01HZZZZZZZZZZZZZZZZZZZZZZB", "Bastogne", hierarchy={"country": "Belgium"}
    )
    (places / "belgium.json").write_text(json.dumps(country))
    (places / "bastogne.json").write_text(json.dumps(town))
    assert link_parent_place_ids(places) == 1
    rec = json.loads((places / "bastogne.json").read_text())
    assert rec["hierarchy"]["parent_place_id"] == country["PlaceID"]
    assert rec["_last_updated"] == dt.date.today().isoformat()
    assert rec["_schema_version"] == entity_version("places")


def test_a_write_without_entity_is_stamped_from_its_directory(tmp_path):
    f = tmp_path / "places" / "p.json"
    assert write_json_with_lock(f, _place("01HZZZZZZZZZZZZZZZZZZZZZZC", "Wiltz"))
    rec = json.loads(f.read_text())
    assert rec["_last_updated"] != OLD and rec["_schema_version"] == entity_version(
        "places"
    )


def test_a_locked_merge_is_stamped(tmp_path):
    f = tmp_path / "places" / "p.json"
    f.parent.mkdir()
    f.write_text(json.dumps(_place("01HZZZZZZZZZZZZZZZZZZZZZZD", "Clervaux")))
    with locked_json(f) as (data, save):
        data["aliases"] = ["Clerf"]
        assert save(data)
    rec = json.loads(f.read_text())
    assert rec["_last_updated"] != OLD and rec["_schema_version"] == entity_version(
        "places"
    )


@pytest.mark.parametrize("name", ["index.json", ".processed_events.json"])
def test_a_sidecar_is_never_stamped(tmp_path, name):
    f = tmp_path / "places" / name
    write_json_with_lock(f, {"bastogne": "bastogne.json"})
    assert "_last_updated" not in json.loads(f.read_text())


def test_a_refused_parent_link_is_not_counted(tmp_path, monkeypatch, caplog):
    import src.extraction.places as pl

    places = tmp_path / "places"
    places.mkdir()
    country = _place("01HZZZZZZZZZZZZZZZZZZZZZZE", "France", geography_type="country")
    town = _place(
        "01HZZZZZZZZZZZZZZZZZZZZZZF", "Sedan", hierarchy={"country": "France"}
    )
    (places / "france.json").write_text(json.dumps(country))
    (places / "sedan.json").write_text(json.dumps(town))
    monkeypatch.setattr(pl, "write_json_with_lock", lambda *a, **k: False)
    assert pl.link_parent_place_ids(places) == 0
    assert "OBSERV places.parent_link_refused place=sedan.json" in caplog.text
    assert "linked=0 refused=1 status=warn" in caplog.text
