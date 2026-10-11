"""Validate the committed sample fixtures against their schemas.

Each category under ``tests/fixtures/validation/<schema>/sample.json`` holds a
wrapper object with TWO records: a MINIMAL record (required fields only — proves
nothing optional is secretly required) and a FULLY-EXPANDED record (every optional
field populated — proves the schema accepts the full current shape). A schema
change that breaks either shape fails here, in the local gate, before it can reach
production data. See tests/fixtures/validation/README.md.
"""

import json
from pathlib import Path

import pytest

from src.utils.schema_registry import get_registry

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "validation"


def _fixture_dirs():
    if not FIXTURE_ROOT.is_dir():
        return []
    return sorted(d for d in FIXTURE_ROOT.iterdir() if d.is_dir())


@pytest.mark.parametrize("schema_dir", _fixture_dirs(), ids=lambda d: d.name)
def test_fixture_validates_against_schema(schema_dir):
    import jsonschema

    schema_name = schema_dir.name
    registry = get_registry()
    assert (
        schema_name in registry.list_schemas()
    ), f"fixture dir '{schema_name}' has no matching schema in the registry"
    schema = registry.get_schema(schema_name)

    minimal_f = schema_dir / "minimal.json"
    expanded_f = schema_dir / "expanded.json"
    assert (
        minimal_f.is_file() and expanded_f.is_file()
    ), f"missing minimal.json/expanded.json in {schema_dir}"
    minimal = json.loads(minimal_f.read_text(encoding="utf-8"))
    expanded = json.loads(expanded_f.read_text(encoding="utf-8"))

    # Authoritative per-entity model: each file is a SINGLE standalone record validated directly
    # against the entity schema (same path validate_directory + the CI validate step use — one
    # record per file, no wrapper). minimal proves nothing optional is secretly required; expanded
    # proves the full current shape is accepted.
    for rec in (minimal, expanded):
        jsonschema.validate(instance=rec, schema=schema)
    assert _leaf_count(expanded) > _leaf_count(
        minimal
    ), f"{schema_name}: expanded.json should be strictly richer than minimal.json"


def test_every_fixture_dir_maps_to_a_real_schema():
    registry = get_registry()
    known = set(registry.list_schemas())
    for d in _fixture_dirs():
        assert d.name in known, f"orphan fixture dir: {d.name}"


def _leaf_count(obj):
    if isinstance(obj, dict):
        return sum(_leaf_count(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_leaf_count(v) for v in obj)
    return 0 if obj in (None, "", [], {}) else 1
