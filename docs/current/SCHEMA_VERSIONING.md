# JSON Schema Versioning — What To Bump, When

**Source of version truth:** `src/schemas/__init__.py::ENTITY_SCHEMA_VERSIONS` — a central map
of **per-entity** versions (each entity has its OWN version). A change to one entity bumps
ONLY that entity; the others are untouched (no cross-entity coupling, nothing "left behind").
`SCHEMA_VERSION` remains as a deprecated alias (= the max across the map) for un-migrated
callers; new code uses `entity_version("<entity>")`.

Everything derives from this one map: each schema module's `"version"` field
(`entity_version("places")`), each extractor's `SCHEMA_TARGET` (`entity_version("places")`),
and the guards. Records are stamped `_schema_version` with their entity's version at the
shared write layer (`inject_metadata(data, entity=...)`).

---

## When to bump an entity's version

Bump the ONE entity you changed (edit its line in `ENTITY_SCHEMA_VERSIONS`) when you change
the SHAPE of its enforced schema: add/remove/rename a field, change a type/nullability, change
an enum, or change `required`/`additionalProperties`. Do NOT bump for doc/prompt/refactor
changes that don't change output shape, or for index/report files.

### Additive vs. breaking (owner rule — determines reprocessing)

- **ADDITIVE** (new **optional** field, widened type): bump that entity + re-pin its
  fingerprint. **No reprocessing** — existing records remain valid, confirmed automatically
  by `tests/test_entity_schema_consistency.py` (old records still validate against the new
  schema).
- **BREAKING** (new **required** field, removed/renamed field, tightened type): bump that
  entity AND either register `schema_contract.register_upgrade(old, new)` OR run a **targeted
  reprocess of that one entity**. Never a global reprocess; other entities are not touched.
  The consistency test will FAIL on old records until upgraded/reprocessed — that failure is
  the signal that a breaking change needs one of those two actions.

### The guard chain (all per-entity)

- `tests/test_schema_fingerprint.py` — a schema SHAPE change that doesn't bump THAT entity's
  version fails the build (fingerprint excludes the version field, so a version bump alone
  doesn't trip it). Pins are per-entity: `(version, fingerprint)`.
- `tests/test_schema_target_guard.py` — each read-rewrite module's `SCHEMA_TARGET` must equal
  ITS entity's version; a new writer with no target fails.
- `tests/test_entity_schema_consistency.py` — real records of each entity validate against
  its enforced schema (catches drift; is the additive-vs-breaking gate above).

---

## Comprehensive entity → schema map

Every JSON entity type, its enforced schema module, its output location, and whether code
reads-then-rewrites it (= has a `SCHEMA_TARGET` contract that must track the version).

| Entity | Output location | Enforced schema module | Read-rewrite contract (SCHEMA_TARGET) |
|---|---|---|---|
| Events | `output/content/{Book}/*-event.json` | `events_output.py` | — (written whole) |
| Dates | `output/dates/*.json` | `dates_output.py` | ✅ `dates.py` |
| Places | `output/places/*.json` | `places_output.py` | ✅ `places.py` |
| People | `output/people/*.json` | `people_output.py` | ✅ `people.py` |
| People-groups | `output/people_groups/*.json` | `groups_output.py` | ✅ `people_groups.py` |
| Equipment | `output/equipment/*.json` | `equipment_output.py` | ✅ `equipment.py` |
| Weather | `output/weather/*.json` | `weather_output.py` | ✅ `weather_central.py` |
| Logistics | `output/logistics/*.json` | `logistics_output.py` | — (written whole) |
| Casualties | `output/casualties/*.json` | `casualties_output.py` | — (written whole) |
| Maps (catalog) | `output/maps/*.json` | `maps_output.py` | — |
| Map features | `output/map_features/*.json` | `map_features_output.py` | — (prototype) |
| Bibliography | `output/bibliography/*.json` | `bibliography_output.py` | — |
| Images | `output/images/*.json` | `images_output.py` | — |
| Batch mentions | (per-entity dirs) | per-entity | ✅ `batch_parallel.py` |

**Images** (`output/images/*.json`) — enforced by `images_output.py` (added; all 567 real
records validate). No unenforced entity types remain.

**Not entity records (no schema / no version concern):** `index.json`,
`duplicate_report.json`, `not_duplicates.json`, `.processed_events.json`,
`output/metrics/*.json`.

---

## Checklist when changing an entity's JSON shape

1. [ ] Edit the enforced `*_output.py` schema.
2. [ ] Bump THAT entity's version in `ENTITY_SCHEMA_VERSIONS` (src/schemas/__init__.py).
3. [ ] SCHEMA_TARGET auto-derives from the map; for a BREAKING change register a
   `schema_contract.register_upgrade(old, new)` or targeted-reprocess that entity.
4. [ ] Update `docs/current/SCHEMA_REFERENCE.md` (version header + the entity's field table).
5. [ ] Validate real `output/<entity>/*.json` records against the updated schema (0 drift).
6. [ ] Run the guard test (`tests/test_schema_target_guard.py`) — must pass.

---

## Machine-checkable registry (code)

The entity list above is mirrored in code at `src/schemas/entity_registry.py`
(`ENTITY_REGISTRY`), and `tests/test_entity_schema_consistency.py` validates real records of
each entity against its enforced schema — so code↔schema drift (an extractor writing a field
the strict schema forbids) fails the build. Add a new entity to the registry when you add a
new JSON entity type.
