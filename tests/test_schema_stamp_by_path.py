"""Schema-version stamping is correct for every writer, with or without an explicit entity.

XChannel-8 dev E2E (2026-11-06): ~30 write_json_with_lock callers omitted entity=, so every record
was stamped the MINIMUM version (2.24). events/logistics/casualties then failed the validator's A1
check (_schema_version != target), and every entity index.json was polluted with _schema_version /
_last_updated keys as if they were index entries. The fix resolves the entity from the path when the
caller omits it, and never stamps a non-record file.
"""

import json

import pytest

from src.schemas import ENTITY_SCHEMA_VERSIONS
from src.utils import file_lock
from src.utils.file_lock import write_json_with_lock


@pytest.fixture(autouse=True)
def _no_guard(monkeypatch):
    # Isolate stamping from schema validation (tested elsewhere): always allow the write.
    monkeypatch.setattr(file_lock, "_validate_entity", lambda *a, **k: True)
    monkeypatch.delenv("NOTIFICATION_TOPIC_ARN", raising=False)


def _write(tmp_path, rel, data, **kw):
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    assert write_json_with_lock(f, data, **kw) is True
    return json.loads(f.read_text())


def test_entity_inferred_from_path_when_caller_omits_it(tmp_path):
    """The ~30-caller bug: no entity= must still stamp the entity's OWN version, not the minimum."""
    for entity, ver in ENTITY_SCHEMA_VERSIONS.items():
        if entity in ("events", "maps"):  # special paths, covered below
            continue
        out = _write(tmp_path / entity, f"{entity}/rec.json", {"x": 1})
        assert out["_schema_version"] == ver, entity


def test_event_file_stamped_events_version(tmp_path):
    out = _write(
        tmp_path, "content/USA-E-XChannel-8/chapter1full-event.json", {"Event": {}}
    )
    assert out["_schema_version"] == ENTITY_SCHEMA_VERSIONS["events"]


def test_explicit_entity_still_wins(tmp_path):
    out = _write(tmp_path, "people/p.json", {"x": 1}, entity="people_groups")
    assert out["_schema_version"] == ENTITY_SCHEMA_VERSIONS["people_groups"]


@pytest.mark.parametrize(
    "name", ["index.json", "duplicate_report.json", ".processed_events.json"]
)
def test_non_record_files_are_never_stamped(tmp_path, name):
    """A name->file index (or a report/dotfile) must round-trip without stamp keys injected."""
    out = _write(tmp_path / "people", f"people/{name}", {"Omar Bradley": "omar.json"})
    assert "_schema_version" not in out and "_last_updated" not in out


def test_unknown_location_keeps_prior_fallback(tmp_path):
    """An unregistered dir is not a known entity: prior behaviour (stamp the minimum version)."""
    from src.schemas import entity_version

    out = _write(tmp_path, "scratch/thing.json", {"x": 1})
    assert out["_schema_version"] == entity_version(None)


def test_explicit_entity_never_stamps_a_non_record_file(tmp_path):
    """Passing entity= for an index must not re-introduce the stamp-key pollution."""
    out = _write(
        tmp_path / "people", "people/index.json", {"A": "a.json"}, entity="people"
    )
    assert "_schema_version" not in out


@pytest.mark.parametrize("name", ["coords.json", "not_groups.json"])
def test_known_sidecars_are_not_records(tmp_path, name):
    out = _write(tmp_path / "places", f"places/{name}", {"k": "v"})
    assert "_schema_version" not in out
