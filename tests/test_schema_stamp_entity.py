"""Regression — §1.0c CRITICAL: schema-version stamping must honor the ENTITY.

Before: ecs_entrypoint._stamp_file called needs_migration/inject_metadata with NO entity, so both fell
back to entity_version(None) = the MIN version (2.24). A places record (real version 2.27) was seen as
"needs migration" and re-stamped DOWN to 2.24 -> permanently "needs_upgrade" on the write guard's
lenient path. And a single global weather-only fast-path early-returned (weather's version == the min)
so higher-versioned dirs were never stamped. Now the dir name is threaded as the entity, and the
fast-path is per-entity.
"""

import json
import os

os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import ecs_entrypoint as E
from src.schemas import (
    inject_metadata,
    needs_migration,
    entity_version,
)


def test_entity_version_distinguishes_entities():
    # sanity: these differ, so a None-fallback (min) is demonstrably wrong for places
    assert entity_version("places") == "2.27"
    assert entity_version("weather") == "2.24"
    assert entity_version(None) == "2.24"  # the MIN — the old buggy fallback


def test_stamp_file_threads_entity_stamps_real_version(tmp_path):
    # an OLD places record -> must be stamped to places' REAL version 2.27 (not 2.24)
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"PlaceID": "X", "_schema_version": "2.0"}))
    n = E._stamp_file(f, needs_migration, inject_metadata, "places")
    assert n == 1
    assert json.loads(f.read_text())["_schema_version"] == "2.27"


def test_stamp_file_does_not_downgrade_current_places(tmp_path):
    # a places record ALREADY at 2.27 must NOT be re-stamped (the downgrade-to-2.24 bug)
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"PlaceID": "X", "_schema_version": "2.27"}))
    n = E._stamp_file(f, needs_migration, inject_metadata, "places")
    assert n == 0
    assert json.loads(f.read_text())["_schema_version"] == "2.27"


def test_dir_all_current_is_per_entity(tmp_path):
    # places files at 2.27 are "all current" for places (so the dir is skippable) — the OLD global
    # weather sample would have wrongly driven this decision.
    (tmp_path / "a.json").write_text(json.dumps({"_schema_version": "2.27"}))
    (tmp_path / "b.json").write_text(json.dumps({"_schema_version": "2.27"}))
    files = list(tmp_path.glob("*.json"))
    assert E._dir_all_current(files, "places", needs_migration) is True
    # the SAME files judged against weather (2.24) are NOT current -> would be stamped (shows the
    # check is per-entity, not a single global version)
    assert E._dir_all_current(files, "weather", needs_migration) is False


def test_stamp_entity_dir_stamps_higher_versioned_dir(tmp_path):
    # the old global fast-path (weather==min) early-returned and never stamped places; the per-dir
    # path stamps an out-of-date places dir to 2.27.
    p = tmp_path / "places"
    p.mkdir()
    (p / "one.json").write_text(json.dumps({"PlaceID": "A", "_schema_version": "2.1"}))
    (p / "two.json").write_text(json.dumps({"PlaceID": "B", "_schema_version": "2.27"}))
    (p / "index.json").write_text("{}")  # skipped
    n = E._stamp_entity_dir(
        p, "places", {"index.json"}, needs_migration, inject_metadata
    )
    assert n == 1  # only the out-of-date one stamped; index.json skipped
    assert json.loads((p / "one.json").read_text())["_schema_version"] == "2.27"
    assert json.loads((p / "two.json").read_text())["_schema_version"] == "2.27"


def test_stamp_entity_dir_skips_all_current(tmp_path):
    p = tmp_path / "places"
    p.mkdir()
    (p / "one.json").write_text(json.dumps({"_schema_version": "2.27"}))
    n = E._stamp_entity_dir(p, "places", set(), needs_migration, inject_metadata)
    assert n == 0  # all current -> fast-path skip
