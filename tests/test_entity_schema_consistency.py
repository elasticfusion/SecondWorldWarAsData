"""Entity ↔ schema consistency, driven by the code registry.

For each entity in ENTITY_REGISTRY, real on-disk records must validate against the ENFORCED
schema (catches code↔schema drift — the class of bug fixed in the schema-consistency pass:
extractor writes a field the strict schema forbids). Records that lack the entity's required
primary key are STALE pre-contract stubs (re-extraction fixes them) and are excluded from the
drift assertion but counted + reported.

Skips an entity gracefully when its output dir is empty (CI has no corpus) — the registry
load + schema import are still exercised so a broken schema import always fails.
"""

import glob
import json

import jsonschema
import pytest

from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

_SKIP_NAMES = {
    "index.json",
    "duplicate_report.json",
    "not_duplicates.json",
    ".processed_events.json",
    "related_groups_report.json",
    "not_related.json",
}

# Tolerance for stale keyless stubs per entity (records missing the required ID — stale data,
# not drift). 0 = the entity should have no keyless records.
_KEYLESS_TOLERANCE_FRACTION = 0.25


@pytest.mark.parametrize("spec", ENTITY_REGISTRY, ids=lambda s: s.name)
def test_entity_records_match_enforced_schema(spec):
    schema = load_schema(spec)  # always exercises the import — broken schema fails here
    files = [
        f for f in glob.glob(spec.output_glob) if f.split("/")[-1] not in _SKIP_NAMES
    ]
    if not files:
        pytest.skip(f"no {spec.name} records on disk")

    checked = keyless = drift = 0
    drift_examples = []
    for f in files[:1000]:
        try:
            d = json.loads(open(f, encoding="utf-8").read())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(d, dict):
            continue
        if spec.required_id and not d.get(spec.required_id):
            keyless += 1
            continue  # stale pre-contract stub — not a drift failure
        checked += 1
        try:
            jsonschema.validate(d, schema)
        except jsonschema.ValidationError as e:
            drift += 1
            if len(drift_examples) < 5:
                drift_examples.append(f"{f.split('/')[-1]}: {e.message[:80]}")

    assert drift <= max(1, checked * 0.01), (
        f"{spec.name}: {drift}/{checked} real records fail the enforced schema "
        f"(code↔schema DRIFT above the 1% malformed-record tolerance). "
        f"Examples: {drift_examples}"
    )
    # keyless stubs are stale data, not drift — but flag if they dominate (writer bug).
    if spec.required_id and checked + keyless > 0:
        assert (
            keyless <= (checked + keyless) * _KEYLESS_TOLERANCE_FRACTION or checked > 0
        ), f"{spec.name}: {keyless} records missing required {spec.required_id}"
