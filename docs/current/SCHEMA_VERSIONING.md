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
7. [ ] ARCHIVE the new shape to `src/schemas/history/<entity>/<version>.json`
   (see `src/schemas/history/README.md`) — the diffable twin of the fingerprint pin.
8. [ ] Add a row to the **Change Log** below (version, additive/breaking, backward-compat note).

---

## Change Log (per-entity shape changes + backward compatibility)

Append a row for every schema SHAPE change. "Compat" = ADDITIVE (existing records stay valid,
no reprocess) or BREAKING (upgrade registered / targeted reprocess). This is the human-readable
companion to the per-entity fingerprint pins in `tests/test_schema_fingerprint.py`.

| Date | Entity | Version | Change | Compat |
|---|---|---|---|---|
| 2026-10-27 | people | 2.26 → 2.27 | `images[]` item gains nullable `SourceSectionID` (durable ULID attribution back to the source_section) — multimodal epic M5 | ADDITIVE — existing people records without the field remain valid (confirmed by test_entity_schema_consistency); no reprocess |
| 2026-10-27 | places | 2.29 → 2.30 | First schema.org adoption (SCHEMA_ORG_REVIEW.md): nullable `sameAs[]` (external-ID cross-walk), `containedInPlace`/`containsPlace` (ID-typed hierarchy edges), `geo` (closed GeoShape: point/line/polygon/circle + provenance), `additionalProperty[]` (typed {propertyID,value,unitCode}), dedicated `elevation_m` | ADDITIVE — all nullable; existing places records remain valid (confirmed by test_entity_schema_consistency); no reprocess |
| 2026-10-27 | images | 2.26 → 2.27 | schema.org ImageObject adoption (SCHEMA_ORG_REVIEW.md §5): nullable contentUrl/thumbnailUrl, creditText/copyrightNotice/copyrightHolder/acquireLicensePage (licensing), contentLocation (typed {PlaceID,name} depicted place), dateCreated/datePublished (when taken/published, distinct from ingestion dates), caption, width/height/encodingFormat/exifData | ADDITIVE — all nullable; existing image records remain valid (test_entity_schema_consistency); no reprocess |
| 2026-10-28 | events | 2.24 → 2.25 | `Event` + each `Sub-event` gain nullable `SourceSectionID` (ULID) — stamped in Phase 3 from the committed source_section records so entity → event → SECTION is a hard, enforceable ID chain (was filename-convention only). Provenance hardening (XChannel-8 E2E). | ADDITIVE — nullable; existing event records without the field remain valid (test_entity_schema_consistency); no reprocess |

---

## Machine-checkable registry (code)

The entity list above is mirrored in code at `src/schemas/entity_registry.py`
(`ENTITY_REGISTRY`), and `tests/test_entity_schema_consistency.py` validates real records of
each entity against its enforced schema — so code↔schema drift (an extractor writing a field
the strict schema forbids) fails the build. Add a new entity to the registry when you add a
new JSON entity type.
- people_groups 2.28 -> 2.29 (ADDITIVE, no reprocess): + organization_lifecycle, active_period{start,end,precision,original_text}, constituent_units — ephemeral mission-scoped orgs (naval task forces: date-bound + compositional). Unknown dates allowed (precision=unresolved) per the capture-despite-unknowns principle.
- casualties 2.26 -> 2.27 (ADDITIVE, no reprocess): + magnitude (none/light/moderate/heavy/severe), + is_comparative — qualitative/comparative/rate/absence casualty granularity (gold-standard review: 27+ casualty mentions, 2 extracted).
- logistics 2.24 -> 2.25 (ADDITIVE): + original_text (verbatim provenance anchor). Paired with prompt positive-logistics + per-item-shortage + clamp-set expansion (_VALID_TYPES/_CATEGORIES).
- equipment 2.25 -> 2.26 (ADDITIVE): + presence + absence_reason to the OUTPUT schema root. REGRESSION FIX: #652 added these to the model+builder but not the schema -> 18/18 additionalProperties block (total equipment loss, 2026-10-31 measurement run).
