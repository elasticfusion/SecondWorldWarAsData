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
    "events": ("2.24", "bcc344080723f54a"),
    "dates": ("2.24", "28553959a617e5e4"),
    "places": ("2.25", "6330e6a3a4166429"),
    "people": ("2.24", "c73f1d99c5c80b61"),
    "people_groups": ("2.25", "8929cee5bcc2dd1c"),
    "equipment": ("2.25", "80ec735d8a6d009b"),
    "weather": ("2.24", "0f3460daa0d8519d"),
    "logistics": ("2.24", "0ca708c99ab0dc40"),
    "casualties": ("2.24", "a516e260ed490b55"),
    "maps": ("2.24", "3b81288612b70e10"),
    "map_features": ("2.24", "7cf064901b370f2d"),
    "bibliography": ("2.24", "3bf1e7f997304516"),
    "images": ("2.25", "f82a6817ffe2628e"),
    "source_section": ("2.25", "d1d9ed1c851bc376"),
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
        f"{spec.name} schema SHAPE changed (fingerprint {actual} != pinned {expected}). "
        f"A shape change MUST bump THIS entity's version in ENTITY_SCHEMA_VERSIONS, then "
        f"regenerate its pin via tests.test_schema_fingerprint._print_pins()."
        f"tests.test_schema_fingerprint._print_pins()."
    )
