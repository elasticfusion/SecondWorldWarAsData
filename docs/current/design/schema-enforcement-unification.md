# Design decision: unify schema enforcement (json_schemas.py ↔ src/schemas registry)

Status: PROPOSED (PR-B of the "same validators across the project" audit; PR-A consolidated the ULID pattern).

## Problem

Two schema systems coexist:

1. **`src/json_schemas.py`** — 15 hand-written JSON-schema dicts (868 LOC). Used for
   **extraction-time** validation of the LLM *response* (`jsonschema.validate(instance=llm_json,
   schema=X_SCHEMA)` — fail-fast before building records).
2. **`src/schemas/` registry** — `ENTITY_REGISTRY` + `*_output.py` + `ulid_field()` + `load_schema()`.
   Used by the **write-time** guard (`file_lock._validate_entity` → `load_schema`) that blocks a
   schema-invalid record from being persisted.

Risk: two definitions of "valid X" that can drift (the audit's "same code all phases" concern).

## Findings (evidence)

- **4 of 15 json_schemas.py schemas are TRULY DEAD** (zero references anywhere incl. tests):
  `WEATHER_SCHEMA`, `LOGISTICS_SCHEMA`, `BIBLIOGRAPHY_SCHEMA`, and `IMAGES_SCHEMA` (referenced only
  in an `images_output.py` doc comment). These are safe to retire with no behavior change.
- **7 are TEST-COUPLED** (no PRODUCTION importer, but tests validate sample data against them —
  `test_json_validator.py`, `test_integration.py`, `test_schemas.py`, `test_validation_performance.py`):
  `DATE`, `PLACE`, `PEOPLE`, `PEOPLE_GROUPS`, `EQUIPMENT`, `MAP`, `CASUALTIES`. Production uses the
  registry for these entities; the legacy schema survives only as a test fixture. Removing them
  requires migrating those tests to the registry schema → Phase 2 (NOT a pure cruft removal).
- **4 are LIVE in production**, each imported once for **extraction-time** validation of the LLM
  response (a DIFFERENT enforcement point + often a different shape than the persisted record):
  - `EVENT_SCHEMA` → `events.py:58`; `SUPPLEMENTAL_SCHEMA` → `supplemental.py:387`;
    `CASUALTY_ITEM_SCHEMA` → `casualties.py:482`; `PEOPLE_GROUP_ITEM_SCHEMA` → `people_groups.py:212`.
- `events`, `casualties`, `people_groups` **have** registry `*_output.py` schemas (write-time);
  **`supplemental` does NOT** (not in `ENTITY_REGISTRY`) → its legacy schema is its SOLE schema.

## Decision

The two systems are **complementary, not purely redundant** (extraction-time response validation vs
write-time record validation), so a wholesale "delete json_schemas.py and point everything at the
registry" is WRONG — it would conflate two shapes/points and risk changing validation semantics.

Phased, semantics-preserving plan:

- **Phase 1 (this PR-B): retire the 4 TRULY-DEAD schemas** (WEATHER, LOGISTICS, BIBLIOGRAPHY, IMAGES).
  Zero references anywhere → pure cruft removal, no behavior change, no test change.
- **Phase 2 (follow-up): retire the 7 TEST-COUPLED schemas** (DATE, PLACE, PEOPLE, PEOPLE_GROUPS,
  EQUIPMENT, MAP, CASUALTIES). Production already uses the registry for these; the legacy schema
  survives only as a test fixture. Migrate `test_json_validator`/`test_integration`/`test_schemas`/
  `test_validation_performance` to validate against the registry `load_schema(spec)`, then delete the
  legacy schema. Needs a per-entity semantic diff (legacy response-shape vs registry record-shape may
  differ) so the migrated tests assert the right thing — NOT a blind delete.
- **Phase 3 (follow-up): single-source the 4 LIVE extraction-time schemas** next to their registry
  entity (e.g. an `EXTRACTION_RESPONSE_SCHEMA` in the entity's `*_output.py`), and **register
  `supplemental`** in `ENTITY_REGISTRY` (+ `supplemental_output.py`) so its write path is guarded.

Phases 2–3 require careful semantic diffing and are NOT in PR-B. PR-B does Phase 1 only (the 4 dead).

## Non-goals / invariants

- Do NOT change what any live schema accepts/rejects (no validation-semantics change).
- Do NOT merge the extraction-time and write-time enforcement points — they are deliberately distinct.
- The ULID pattern is already single-source (PR-A).
