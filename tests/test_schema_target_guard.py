"""Guard tests: prevent code that is incompatible with the current JSON schema version.

These FAIL THE BUILD when:
1. a module declaring SCHEMA_TARGET has drifted from the current SCHEMA_VERSION (code was not
   updated when the schema was bumped) — forcing a conscious decision: update the code and
   its target, or register a case-by-case upgrader;
2. a known entity writer (read-then-rewrite via the schema contract) does not declare a
   SCHEMA_TARGET at all (a new writer silently skipping the contract);
3. a module's SCHEMA_TARGET names a version NEWER than the codebase's SCHEMA_VERSION
   (impossible/typo).
"""

import importlib

import pytest

from src.schemas import SCHEMA_VERSION

# Every module that reads-then-rewrites entity records and must honor the schema contract.
# A new entity writer MUST be added here (and declare SCHEMA_TARGET) — the test enforces it.
CONTRACT_MODULES = [
    "src.extraction.people_groups",
    "src.extraction.dates",
    "src.extraction.equipment",
    "src.extraction.people",
    "src.extraction.places",
    "src.extraction.weather_central",
    "src.extraction.batch_parallel",
]


def _version_tuple(v: str) -> tuple:
    return tuple(int(p) for p in str(v).split("."))


@pytest.mark.parametrize("module_name", CONTRACT_MODULES)
def test_contract_module_declares_schema_target(module_name):
    mod = importlib.import_module(module_name)
    assert hasattr(mod, "SCHEMA_TARGET"), (
        f"{module_name} reads+rewrites entity records but declares no SCHEMA_TARGET — "
        "it must declare the schema version it targets (schema-contract directive)."
    )


@pytest.mark.parametrize("module_name", CONTRACT_MODULES)
def test_contract_module_target_is_current(module_name):
    mod = importlib.import_module(module_name)
    target = getattr(mod, "SCHEMA_TARGET", None)
    assert target == SCHEMA_VERSION, (
        f"{module_name} targets schema {target} but current SCHEMA_VERSION is "
        f"{SCHEMA_VERSION}. The code was not updated for the current schema. Either update "
        f"{module_name} for {SCHEMA_VERSION} and bump its SCHEMA_TARGET, or register a "
        f"case-by-case upgrade and advance the target deliberately."
    )


@pytest.mark.parametrize("module_name", CONTRACT_MODULES)
def test_contract_target_not_ahead_of_codebase(module_name):
    mod = importlib.import_module(module_name)
    target = getattr(mod, "SCHEMA_TARGET", None)
    if target is not None:
        assert _version_tuple(target) <= _version_tuple(SCHEMA_VERSION), (
            f"{module_name} SCHEMA_TARGET {target} is NEWER than SCHEMA_VERSION "
            f"{SCHEMA_VERSION} — impossible/typo."
        )
