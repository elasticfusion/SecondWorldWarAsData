"""Schema-fingerprint guard: a schema SHAPE change must bump SCHEMA_VERSION.

Closes the hole where a backward-compatible schema edit (e.g. a new optional field) changes
the shape but breaks no existing record and bumps no version — so records written before and
after carry the same _schema_version despite different shapes.

How it works: each enforced entity schema is canonically serialized + hashed. The expected
fingerprints are PINNED here, keyed by the SCHEMA_VERSION they correspond to. The test fails
when a live schema's fingerprint differs from its pin, telling you to either:
  - bump SCHEMA_VERSION (the shape changed), then update the pins for the new version; or
  - if you deliberately re-pinned without a version change (e.g. a comment-only reorder that
    changed the hash but not the contract), update the pin consciously.

Updating pins is a deliberate, reviewed act — that's the point: no silent shape drift.
"""

import hashlib
import json

import pytest

from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema


def _fingerprint(schema: dict) -> str:
    """Stable hash of a schema's STRUCTURE (sorted keys, version field excluded so a
    SCHEMA_VERSION bump alone doesn't change the fingerprint — only shape does)."""
    clone = {k: v for k, v in schema.items() if k != "version"}
    canonical = json.dumps(clone, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# Per-entity pins: each entity pins BOTH its current version and its schema fingerprint.
# A shape change to ONE entity trips only ITS check (no cross-entity coupling). Regenerate
# the single changed entity's pin after a deliberate shape change + that entity's version
# bump: run  python -c "from tests.test_schema_fingerprint import _print_pins; _print_pins()"
EXPECTED = {
    "events": ("2.25", "3f296ddee5c49d39"),
    "dates": ("2.25", "79f50ab8a186ab18"),
    "places": ("2.30", "1de37dd5ec4fa398"),
    "people": ("2.27", "b46ce4c711d7a1f6"),
    "people_groups": ("2.28", "136c939350cd0a97"),
    "equipment": ("2.25", "80ec735d8a6d009b"),
    "weather": ("2.24", "0f3460daa0d8519d"),
    "logistics": ("2.24", "0ca708c99ab0dc40"),
    "casualties": ("2.26", "577ca60f90666005"),
    "maps": ("2.24", "3b81288612b70e10"),
    "map_features": ("2.24", "7cf064901b370f2d"),
    "bibliography": ("2.29", "acccb2e726dc06ea"),
    "images": ("2.27", "e91cb1b0822e6d54"),
    "source_section": ("2.26", "f19088e6d91b3aac"),
}


def _print_pins():
    """Regenerate the per-entity pin block (version + fingerprint from the central map)."""
    from src.schemas import entity_version

    print("EXPECTED = {")
    for spec in ENTITY_REGISTRY:
        v = entity_version(spec.name)
        print(f'    "{spec.name}": ("{v}", "{_fingerprint(load_schema(spec))}"),')
    print("}")


def test_pins_track_current_entity_versions():
    from src.schemas import entity_version

    mismatched = {
        name: (pinned_v, entity_version(name))
        for name, (pinned_v, _fp) in EXPECTED.items()
        if pinned_v != entity_version(name)
    }
    assert not mismatched, (
        f"pinned version != current entity version for: {mismatched}. Regenerate that "
        "entity's pin (tests.test_schema_fingerprint._print_pins)."
    )


@pytest.mark.parametrize("spec", ENTITY_REGISTRY, ids=lambda s: s.name)
def test_schema_shape_matches_pinned_fingerprint(spec):
    expected = EXPECTED.get(spec.name, (None, None))[1]
    actual = _fingerprint(load_schema(spec))
    assert expected == actual, (
        f"{spec.name} schema SHAPE changed (fingerprint {actual} != pinned {expected}).\n"
        f"REMINDER — the documented schema-change workflow (docs/current/SCHEMA_VERSIONING.md):\n"
        f"  1. Bump THIS entity's version in src/schemas/__init__.py::ENTITY_SCHEMA_VERSIONS.\n"
        f"  2. Classify the change: ADDITIVE (new OPTIONAL field / widened type) -> no reprocess;\n"
        f"     BREAKING (new required / removed / renamed / tightened) -> register an upgrade or\n"
        f"     targeted-reprocess that one entity (test_entity_schema_consistency gates this).\n"
        f"  3. Regenerate this entity's pin: "
        f"python -c 'from tests.test_schema_fingerprint import _print_pins; _print_pins()'\n"
        f"  4. ARCHIVE the new shape: src/schemas/history/{spec.name}/<version>.json "
        f"(see src/schemas/history/README.md) — the diffable twin of this pin.\n"
        f"  5. Record the change + backward-compatibility note in the Change Log "
        f"(docs/current/SCHEMA_VERSIONING.md)."
    )


def test_current_version_is_archived_in_history():
    """Every entity's CURRENT pinned version must have a faithful archived shape under
    src/schemas/history/<entity>/<version>.json (fingerprint == pin). Keeps a durable, diffable
    history of schema evolution (owner requirement)."""
    import pathlib

    root = pathlib.Path("src/schemas/history")
    missing, mismatched = [], []
    for spec in ENTITY_REGISTRY:
        version, pin = EXPECTED.get(spec.name, (None, None))
        if not version:
            continue
        hf = root / spec.name / f"{version}.json"
        if not hf.exists():
            missing.append(str(hf))
            continue
        archived = json.loads(hf.read_text(encoding="utf-8"))
        if _fingerprint(archived) != pin:
            mismatched.append(
                f"{spec.name} {version}: {_fingerprint(archived)} != pin {pin}"
            )
    assert not missing, (
        f"missing archived schema history for current versions: {missing}. "
        "Snapshot via src/schemas/history/README.md."
    )
    assert not mismatched, f"archived history shape != pin: {mismatched}"
