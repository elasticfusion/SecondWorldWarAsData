"""Per-entity enforcement of the central write guard across ALL enforced entities.

For every entity in the registry whose records are written through write_json_with_lock into
a flat ``output/<entity>/`` directory, assert the guard:
  (a) writes a minimal schema-valid record,
  (b) repairs an empty-string ULID primary key instead of persisting ""/blocking, and
  (c) BLOCKS a record that is missing a required field (does not persist it).

This locks the guard so it can't silently regress for any single entity.
"""

import json

import jsonschema
import pytest

from src.schemas import entity_version
from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema
from src.utils.file_lock import write_json_with_lock

# A valid 26-char ULID for seeding records.
_ULID = "01HX7YZABCDEFGHJKMNPQRSTVW"

# Entities whose output is a flat output/<name>/*.json dir governed by the guard.
# Excluded: 'events' (written to content/<book>/*-event.json via a different path, has a
# nested 'Event' required object); 'map_features' (GeoJSON FeatureCollection, Grok-vision
# prototype, not pipeline-wired); 'weather' (deeply-nested required objects — e.g.
# location.place_name — make a generic minimal record impractical here; covered by its own
# weather tests + the schema-consistency guard). The guard mechanics are identical for all
# entities; the remaining 11 exercise it comprehensively.
_SKIP = {"events", "map_features", "weather"}
_SPECS = [s for s in ENTITY_REGISTRY if s.name not in _SKIP]


def _minimal_valid_record(spec):
    """Build the smallest record that satisfies an entity's required fields + ULID patterns."""
    schema = load_schema(spec)
    props = schema.get("properties", {})
    rec = {}
    for field in schema.get("required", []):
        spec_f = props.get(field, {})
        pattern = str(spec_f.get("pattern", ""))
        if pattern.endswith("{26}$"):
            rec[field] = _ULID
        elif spec_f.get("enum"):
            # pick the first allowed enum value (e.g. weather source_type)
            rec[field] = next(
                (v for v in spec_f["enum"] if v is not None), spec_f["enum"][0]
            )
        else:
            # non-ULID required field: supply a plausible typed value
            t = spec_f.get("type")
            t = t[0] if isinstance(t, list) else t
            rec[field] = {
                "string": "x",
                "object": {},
                "array": [],
                "number": 0,
                "boolean": False,
            }.get(t, "x")
    rec["_schema_version"] = entity_version(spec.name)
    rec["_last_updated"] = "2026-10-08"
    return rec


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.name)
def test_minimal_valid_record_writes(spec, tmp_path):
    d = tmp_path / spec.name
    d.mkdir()
    rec = _minimal_valid_record(spec)
    # sanity: the minimal record is actually schema-valid
    jsonschema.validate(rec, load_schema(spec))
    write_json_with_lock(d / "rec.json", rec, entity=spec.name)
    assert (d / "rec.json").exists(), f"{spec.name}: valid record should be written"


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.name)
def test_empty_ulid_pk_is_repaired(spec, tmp_path):
    if not spec.required_id:
        pytest.skip(f"{spec.name} has no simple ULID primary key")
    d = tmp_path / spec.name
    d.mkdir()
    rec = _minimal_valid_record(spec)
    rec[spec.required_id] = ""  # empty ULID PK
    write_json_with_lock(d / "rec.json", rec, entity=spec.name)
    assert (
        d / "rec.json"
    ).exists(), f"{spec.name}: empty-ULID record should be repaired+written"
    saved = json.loads((d / "rec.json").read_text())
    assert (
        saved[spec.required_id] and saved[spec.required_id] != ""
    ), f"{spec.name}: empty {spec.required_id} must be repaired to a real ULID"


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.name)
def test_missing_required_field_is_blocked(spec, tmp_path):
    required = load_schema(spec).get("required", [])
    if not required:
        pytest.skip(f"{spec.name} has no required fields")
    d = tmp_path / spec.name
    d.mkdir()
    rec = _minimal_valid_record(spec)
    rec.pop(required[0])  # drop a required field
    write_json_with_lock(d / "rec.json", rec, entity=spec.name)
    assert not (
        d / "rec.json"
    ).exists(), f"{spec.name}: record missing required '{required[0]}' must be BLOCKED"
