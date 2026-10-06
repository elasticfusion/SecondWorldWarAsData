# JSON Schema Versioning — What To Bump, When

**Single source of version truth:** `src/schemas/__init__.py::SCHEMA_VERSION` (currently
2.24). **All entity schemas share this one constant** — there are no per-entity versions.
Changing any enforced output schema is a schema change and MUST bump `SCHEMA_VERSION`.

Records are stamped `_schema_version` automatically at the shared write layer
(`src/utils/file_lock.write_json_with_lock` and `json_validator`, both call
`inject_metadata`). Code that reads-then-rewrites a record declares `SCHEMA_TARGET` and uses
the schema contract (`src/schemas/schema_contract.py`); the guard test
`tests/test_schema_target_guard.py` fails the build if a module's `SCHEMA_TARGET` drifts from
`SCHEMA_VERSION`.

---

## When to bump SCHEMA_VERSION

Bump when you change the SHAPE of any enforced schema below:
- add / remove / rename a field,
- change a field's type or nullability,
- change an enum's allowed values,
- change `required` or `additionalProperties`.

Do NOT bump for: doc edits, prompt wording, code refactors that don't change output shape,
or new *index/report* files (not entity records).

After bumping, EITHER update every affected `SCHEMA_TARGET` to the new version AND the code
for the new shape, OR register a case-by-case upgrader
(`schema_contract.register_upgrade`). The guard test enforces this.

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
| Batch mentions | (per-entity dirs) | per-entity | ✅ `batch_parallel.py` |

**Gap (unenforced):** **Images** (`output/images/*.json`) has **no** `images_output.py`
enforced schema — only the extraction-time `json_schemas.IMAGES_SCHEMA`, which current image
records do NOT satisfy. Changes to image records are therefore NOT version-gated. (Tracked
as a known gap to be enforced.)

**Not entity records (no schema / no version concern):** `index.json`,
`duplicate_report.json`, `not_duplicates.json`, `.processed_events.json`,
`output/metrics/*.json`.

---

## Checklist when changing an entity's JSON shape

1. [ ] Edit the enforced `*_output.py` schema.
2. [ ] Bump `SCHEMA_VERSION` in `src/schemas/__init__.py`.
3. [ ] Update `SCHEMA_TARGET` in the entity's read-rewrite module(s) (if ✅ above) to the new
   version — or register a `schema_contract.register_upgrade(old, new)`.
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
