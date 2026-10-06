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

from src.schemas import SCHEMA_VERSION
from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema


def _fingerprint(schema: dict) -> str:
    """Stable hash of a schema's STRUCTURE (sorted keys, version field excluded so a
    SCHEMA_VERSION bump alone doesn't change the fingerprint — only shape does)."""
    clone = {k: v for k, v in schema.items() if k != "version"}
    canonical = json.dumps(clone, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# Fingerprints pinned to SCHEMA_VERSION. Regenerate deliberately when you intend a shape
# change (and bump SCHEMA_VERSION): run
#   python -c "from tests.test_schema_fingerprint import _print_pins; _print_pins()"
PINNED_VERSION = "2.24"
EXPECTED_FINGERPRINTS = {
    "events": "bcc344080723f54a",
    "dates": "28553959a617e5e4",
    "places": "63ac24491f8f3115",
    "people": "c73f1d99c5c80b61",
    "people_groups": "0b68e307c8b1f01f",
    "equipment": "68652cf777542f82",
    "weather": "0f3460daa0d8519d",
    "logistics": "0ca708c99ab0dc40",
    "casualties": "a516e260ed490b55",
    "maps": "3b81288612b70e10",
    "map_features": "7cf064901b370f2d",
    "bibliography": "3bf1e7f997304516",
}


def _print_pins():
    """Helper to (re)generate the pin block after a deliberate shape change + version bump."""
    print(f'PINNED_VERSION = "{SCHEMA_VERSION}"')
    print("EXPECTED_FINGERPRINTS = {")
    for spec in ENTITY_REGISTRY:
        print(f'    "{spec.name}": "{_fingerprint(load_schema(spec))}",')
    print("}")


def test_pins_track_current_schema_version():
    assert PINNED_VERSION == SCHEMA_VERSION, (
        f"fingerprint pins are for {PINNED_VERSION} but SCHEMA_VERSION is {SCHEMA_VERSION} — "
        "regenerate the pins (tests.test_schema_fingerprint._print_pins) for the new version."
    )


@pytest.mark.parametrize("spec", ENTITY_REGISTRY, ids=lambda s: s.name)
def test_schema_shape_matches_pinned_fingerprint(spec):
    expected = EXPECTED_FINGERPRINTS.get(spec.name)
    actual = _fingerprint(load_schema(spec))
    assert expected == actual, (
        f"{spec.name} schema SHAPE changed (fingerprint {actual} != pinned {expected}). "
        f"A shape change MUST bump SCHEMA_VERSION. Bump it, then regenerate the pins via "
        f"tests.test_schema_fingerprint._print_pins()."
    )
