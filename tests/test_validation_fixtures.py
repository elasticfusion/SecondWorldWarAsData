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


@pytest.mark.parametrize(
    "schema_dir", _fixture_dirs(), ids=lambda d: d.name
)
def test_fixture_validates_against_schema(schema_dir):
    import jsonschema

    schema_name = schema_dir.name
    registry = get_registry()
    assert schema_name in registry.list_schemas(), (
        f"fixture dir '{schema_name}' has no matching schema in the registry"
    )
    schema = registry.get_schema(schema_name)

    sample = schema_dir / "sample.json"
    assert sample.is_file(), f"missing {sample}"
    data = json.loads(sample.read_text(encoding="utf-8"))

    # Must validate cleanly against the registry schema.
    jsonschema.validate(instance=data, schema=schema)

    # Contract: exactly two records (minimal + fully-expanded).
    wrapper_values = [v for v in data.values() if isinstance(v, list)]
    assert wrapper_values, f"{sample} has no array wrapper"
    records = wrapper_values[0]
    assert len(records) == 2, (
        f"{schema_name}: expected 2 records (minimal + expanded), got {len(records)}"
    )
    # The expanded record must be strictly richer than the minimal one.
    assert _leaf_count(records[1]) > _leaf_count(records[0]), (
        f"{schema_name}: second record should be the fully-expanded one"
    )


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
