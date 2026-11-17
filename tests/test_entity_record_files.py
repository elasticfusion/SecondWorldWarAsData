"""Entity dirs hold run state / index / report sidecars next to records: every loader that globs
an entity dir must read only the records (pathlib's glob matches dotfiles)."""

import json
from pathlib import Path

import pytest

from src.utils.entity_records import is_record_file


@pytest.mark.parametrize(
    "name, record",
    [
        ("person_01ABC.json", True),
        (".processed_events.json", False),
        ("index.json", False),
        ("duplicate_report.json", False),
        ("not_people.json", False),
        ("x.json.lock", False),
        (".anything.json", False),
    ],
)
def test_is_record_file(name, record):
    assert is_record_file(Path("output/people") / name) is record


def _dir_with_sidecars(tmp_path, record):
    (tmp_path / ".processed_events.json").write_text(json.dumps(["E1"]))
    (tmp_path / "index.json").write_text("{}")
    (tmp_path / "rec_1.json").write_text(json.dumps(record))
    return tmp_path


def test_people_enrichment_never_enriches_the_state_file(tmp_path, monkeypatch):
    from src.extraction import enrich_biographies as B

    seen = []
    monkeypatch.setattr(
        B, "_enrich_people_parallel", lambda files, *a, **k: seen.extend(files) or 0
    )
    B.enrich_all_people(_dir_with_sidecars(tmp_path, {"name": "A"}), None)
    assert [f.name for f in seen] == ["rec_1.json"]


def test_logistics_index_and_keys_skip_state_files(tmp_path):
    from src.extraction import logistics as L

    d = _dir_with_sidecars(tmp_path, {"event_mentions": [{}]})
    L._rebuild_index(d)
    assert ".processed_events" not in json.loads((d / "index.json").read_text())
    L._load_existing_keys(d)  # a list sidecar would raise inside; must not be read


def test_date_lookups_skip_state_files(tmp_path):
    from src.extraction import casualties as C

    d = _dir_with_sidecars(tmp_path, {"EventID": "E1", "date": "1944-06-06"})
    assert isinstance(C._event_year_lookup(d), dict)
