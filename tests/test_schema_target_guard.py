"""Guard tests: prevent code incompatible with its entity's JSON schema version.

Per-entity (no cross-entity coupling): each read-then-rewrite module targets ONE entity and
its SCHEMA_TARGET must equal THAT entity's version in the central ENTITY_SCHEMA_VERSIONS map.
Bumping one entity's version only trips that entity's module.

FAIL THE BUILD when:
1. a contract module declares no SCHEMA_TARGET (a new writer skipping the contract);
2. a module's SCHEMA_TARGET != its entity's current version (code not updated for the bump);
3. a module's SCHEMA_TARGET is NEWER than its entity's version (impossible/typo).
"""

import importlib

import pytest

from src.schemas import entity_version

# module -> the entity it reads-then-rewrites. A new entity writer MUST be added here.
CONTRACT_MODULE_ENTITY = {
    "src.extraction.people_groups": "people_groups",
    "src.extraction.dates": "dates",
    "src.extraction.equipment": "equipment",
    "src.extraction.people": "people",
    "src.extraction.places": "places",
    "src.extraction.weather_central": "weather",
    "src.extraction.batch_parallel": "events",
}


def _version_tuple(v: str) -> tuple:
    return tuple(int(p) for p in str(v).split("."))


@pytest.mark.parametrize("module_name", list(CONTRACT_MODULE_ENTITY))
def test_contract_module_declares_schema_target(module_name):
    mod = importlib.import_module(module_name)
    assert hasattr(mod, "SCHEMA_TARGET"), (
        f"{module_name} reads+rewrites entity records but declares no SCHEMA_TARGET — "
        "it must declare the schema version it targets (schema-contract directive)."
    )


@pytest.mark.parametrize("module_name", list(CONTRACT_MODULE_ENTITY))
def test_contract_module_target_is_current_for_its_entity(module_name):
    mod = importlib.import_module(module_name)
    entity = CONTRACT_MODULE_ENTITY[module_name]
    target = getattr(mod, "SCHEMA_TARGET", None)
    expected = entity_version(entity)
    assert target == expected, (
        f"{module_name} targets {target} but entity '{entity}' is at {expected}. Update "
        f"{module_name} for the new shape and set SCHEMA_TARGET=entity_version('{entity}'), "
        f"or register a case-by-case upgrade and advance deliberately."
    )


@pytest.mark.parametrize("module_name", list(CONTRACT_MODULE_ENTITY))
def test_contract_target_not_ahead_of_entity(module_name):
    mod = importlib.import_module(module_name)
    entity = CONTRACT_MODULE_ENTITY[module_name]
    target = getattr(mod, "SCHEMA_TARGET", None)
    if target is not None:
        assert _version_tuple(target) <= _version_tuple(entity_version(entity)), (
            f"{module_name} SCHEMA_TARGET {target} is NEWER than entity '{entity}' version "
            f"{entity_version(entity)} — impossible/typo."
        )
