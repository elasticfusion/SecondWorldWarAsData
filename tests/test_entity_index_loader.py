"""DRY P2.1 guard: the identical 'load index.json -> dict, {} if missing' that dates/places/
weather/people each duplicated is now the shared _entity_common.load_json_index. Pin its contract +
that the per-entity loaders delegate.
"""

import json
from pathlib import Path

from src.extraction._entity_common import load_json_index


def test_load_json_index_missing_returns_empty(tmp_path):
    assert load_json_index(tmp_path / "index.json") == {}


def test_load_json_index_reads_dict(tmp_path):
    p = tmp_path / "index.json"
    p.write_text(json.dumps({"alpha": "f1.json", "beta": "f2.json"}), encoding="utf-8")
    assert load_json_index(p) == {"alpha": "f1.json", "beta": "f2.json"}


def test_entity_loaders_delegate(tmp_path):
    # dir-based loaders append index.json; people takes the file directly. All must agree.
    (tmp_path / "index.json").write_text(json.dumps({"k": "v"}), encoding="utf-8")
    from src.extraction.dates import _load_dates_index
    from src.extraction.places import _load_places_index
    from src.extraction.weather_central import _load_weather_index
    from src.extraction.people import _load_people_index

    assert _load_dates_index(tmp_path) == {"k": "v"}
    assert _load_places_index(tmp_path) == {"k": "v"}
    assert _load_weather_index(tmp_path) == {"k": "v"}
    assert _load_people_index(tmp_path / "index.json") == {"k": "v"}
    # missing dir/file -> {}
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _load_dates_index(empty) == {}
    assert _load_people_index(empty / "index.json") == {}
