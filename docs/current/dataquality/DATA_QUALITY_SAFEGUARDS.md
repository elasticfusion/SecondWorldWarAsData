# Data Quality — Safeguards & Guarantees

**Last Updated:** 2026-10-09

This document is the authoritative overview of **how data quality is enforced** in the WWII
data pipeline: what is validated, where, when, and what is deliberately *not* yet guaranteed.
It is written for engineers extending the pipeline and for reviewers auditing data integrity.

> Guiding principle (from the project charter): **verify, don't fabricate; null over fake;
> the source is the authority; preserve provenance.** Data quality is job one — it gates every
> other feature (RAG/search is built on top of the extracted corpus and must not be re-embedded
> because of avoidable corruption).

---

## 1. The central write guard (preventive layer)

**Every entity JSON write is schema-validated before it is persisted.** A schema-invalid record
is **blocked** (not written) and logged — it can never reach disk or S3 through a guarded path.

### 1.1 One primitive, all paths
Validation lives in a **single primitive**, `src/utils/file_lock.py:_validate_entity`, called by
**every** write path:

| Write path | Used by | Guarded |
|---|---|---|
| `write_json_with_lock(path, data, entity=...)` | all extractors, enrichment, converted raw writers | ✅ |
| `Storage.write_json` (`LocalStorage` + `S3Storage`) | UI-driven dedup merge, lambda writers | ✅ |
| `dedup/merge.py` (`_write_entity_guarded`) | automatic **and** UI merges | ✅ |

Previously the dedup/merge path and `Storage.write_json` wrote records with raw `json.dump` /
`write_text`, bypassing validation — that is how schema-invalid fragments (missing primary-key ID
+ name) reached S3. All such raw writers were converted to route through the guard:
`events.py`, `bibliography.py`, `equipment.py`, `enrich_biographies.py`, `batch_parallel.py`,
`supplemental.py`, `bibliography_resolver.py`, `dedup/merge.py`.

### 1.2 Registry-driven — covers all entities, current and future
The guard resolves the entity from the output path and looks up its schema in
`src/schemas/entity_registry.py:ENTITY_REGISTRY`. **All 14 entity types** are enforced
(events, dates, places, people, people_groups, equipment, weather, logistics, casualties, maps,
map_features, bibliography, images, source_section). A new entity added to the registry is
validated automatically — no guard change needed.

- **Events** are the one path exception: they live at `output/content/<Book>/<chapter>-event.json`
  (parent dir is the *book*, not `events`), so the guard resolves them by the **`-event.json`
  filename suffix**.
- **Non-entity files** (`index.json`, `*_report.json`, dotfiles, aggregates) are passed through —
  they carry no entity schema.

### 1.3 Unconditional
There is **no opt-out**. The former `WWII_WRITE_VALIDATION=off` escape hatch was removed — a
schema-invalid record cannot be persisted by disabling the guard.

### 1.4 Fail-safe posture (in-process)
If the registry/schema/validator is unavailable or errors, the in-process guard **fails open**
(allows the write) rather than wedging the whole pipeline on an infra hiccup. This is a deliberate
availability choice; the planned **S3-event Lambda backstop** (§5) is the fail-*loud* detective
layer that still catches anything the in-process guard waved through on error.

### 1.5 No PII in logs
On a block, the guard logs only the **failing JSON path + the violated validator keyword**
(e.g. `pattern at event_mentions/0/EventID`) — never the offending value. Records carry PII
(names, places, biographical text) sourced from LLM output / external URLs, and logs land in
CloudWatch, so record values are never echoed.

---

## 2. Schema-version awareness

Schema versions are **per-entity** (`src/schemas/__init__.py:ENTITY_SCHEMA_VERSIONS`) and are
**not guaranteed additive** — a newer version can make a field required (a *breaking* change, an
explicitly supported category in `SCHEMA_VERSIONING.md`). The guard is therefore version-aware so
it never wrongly blocks a legitimately-old record on rewrite (which would silently lose data):

| Record state (vs. the entity's current version) | Guard behavior |
|---|---|
| **same version** (`ok`) | validate against current schema |
| **older, registered upgrader** (`upgraded`) | apply the upgrade, then validate the upgraded record (and persist the upgraded form) |
| **older, no upgrader** (`needs_upgrade`) | **allow with a warning** — do not lose a legitimately-old record; a targeted reprocess/upgrade is the owner's job |
| **newer than this code** (`future`) | allow **as-is**, never mutate or block (blocking would lose a newer-schema record) |
| **no `_schema_version` at all** | treated as **malformed, not old** → **strict** validation (block if invalid) |

Discernment uses a numeric per-entity version tuple via `schema_contract.read_record`.

### 2.1 The identity floor (non-negotiable invariant)
The lenient `needs_upgrade` path has a hard floor: **the primary-key ID must be present**, even for
legitimately-old records. The PK (`PersonID`/`GroupID`/`PlaceID`/…) was never a version-added
field, so a PK-less record is corrupt in *every* version and is **always blocked**. This closes the
one path that could otherwise admit corruption under a plausible old version stamp.

### 2.2 Validate-before-stamp ordering
`write_json_with_lock` runs the guard **before** `inject_metadata` stamps the current
`_schema_version`. Stamping first would overwrite the record's true incoming version and defeat the
upgrade/old-record logic above.

---

## 3. Record-level invariants enforced

Enforced on every guarded write (per-entity schema + guard logic):
- **Primary-key ID present + valid ULID** (`^[0-9A-HJKMNP-TV-Z]{26}$`).
- **All mandatory fields** per the entity schema (e.g. `dates`→`date_start`, `casualties`→`type`,
  `bibliography`→`title`, `people`→`name`, `weather`→`date`, `logistics`→`logistics_type`,
  `events`→`Event` object with `EventID`+`Sub-events`).
- **Typed/patterned fields** inside declared sub-objects (e.g. nested `event_mentions[].EventID`
  must be a valid ULID; dates match the date pattern).
- **Empty-string ULIDs repaired** before validation (the targeted empty-ID bug class), without
  regenerating non-empty malformed IDs (which would orphan cross-references).

Enrichment is validated too: when a Grok-confirmed URL's result is appended (image / award /
web_result / primary_source), the **whole enriched record** is re-validated on write. Enrichment
sub-objects use `additionalProperties: true` by design, so *declared* fields are type-checked while
additive provenance fields (e.g. Wayback `archived_url` / `retrieved_at`) are allowed.

---

## 4. Deduplication & merges

Both merge paths are now guarded (§1.1):
- **Automatic merge** (end of Phase 2, `_auto_merge_exact_duplicates` → `merge_generic`/`do_merge`):
  exact-duplicate consolidation, writes the merged record through the guard.
- **UI-driven merge** (human review via the dedup UI lambda → `Storage.write_json`): guarded at the
  storage layer.

Merge **preserves enrichment** — `_merge_person` unions `event_mentions` (dedup by `Sub_eventID`)
and `military_awards`/`ranks`/`units_served`, so a confirmed-URL enrichment on a secondary carries
into the primary.

---

## 5. Planned detective & corrective layers (not yet implemented)

The in-process guard is *preventive* but, by nature, can be bypassed by any future raw writer. The
following close that gap at the **storage perimeter** (tracked in `TODO.md`):

1. **S3-event Lambda backstop** — on `ObjectCreated` under `output/`, run the **same**
   `_validate_entity` primitive (no reimplementation, to avoid guard drift); on invalid, **alert**
   (via the `{env}-wwii-phase2-complete` SNS → Slack/email) + **quarantine** to
   `output/_quarantine/`. Mechanical (needs no external context: key→entity, bytes→record,
   schema→bundled code). Detective, not preventive (fires after the write), so it alerts +
   quarantines rather than blocking. **Fail-loud.**
2. **CI grep-gate** — fail the build if new code writes raw `json.dump`/`write_text` to an
   `output/` entity dir, so the next bypass can't merge.
3. **Corpus-level audit job** — periodic scan running the guard across all of `output/` plus
   invariants per-write validation is blind to:
   - **referential integrity** (every cross-ref ID resolves to an existing record);
   - **ID uniqueness** (no two records share a primary key);
   - **no dangling/orphaned mentions**;
   - **dedup consistency** (no un-merged name-variant fragments);
   - a **validation-block-rate metric** (the in-process guard is fail-open, so silent allows on
     validator error need an observable counter).

---

## 6. Known pre-existing data debt

Before the central guard landed, the unguarded merge path wrote schema-invalid records to S3.
A sample audit (people/people_groups/places/dates) found **~35% invalid** — all **version-less,
missing primary-key ID + name**: unresolved per-mention fragments (e.g. name-variants
"anthony mcauliffe" / "anthony c mcauliffe" that should have merged). They hold **unique**
cross-ref mentions, so **deleting them orphans those mentions.**

**Remediation (planned, forward-fix — no destructive backfill):** quarantine → **targeted
reprocess** from each fragment's provenance (book + EventIDs) to regenerate canonical PK'd records
and re-run dedup → purge quarantine only after the mentions land in canonical records. The guard
now **prevents new** such records.

---

## 7. Quick reference — where things live

| Concern | Location |
|---|---|
| Central guard primitive | `src/utils/file_lock.py:_validate_entity` (+ helpers) |
| Storage-layer guard | `src/utils/storage.py` `LocalStorage/S3Storage.write_json` |
| Merge guard | `src/dedup/merge.py:_write_entity_guarded` |
| Entity → schema map | `src/schemas/entity_registry.py:ENTITY_REGISTRY` |
| Per-entity versions | `src/schemas/__init__.py:ENTITY_SCHEMA_VERSIONS` |
| Version upgrade / read contract | `src/schemas/schema_contract.py:read_record` / `register_upgrade` |
| Version/additive-vs-breaking policy | `docs/current/SCHEMA_VERSIONING.md` |
| Corpus status + known issues | `docs/current/DATA_QUALITY_STATUS.md` |
| Guard tests | `tests/test_central_write_guard.py`, `tests/test_write_guard_*` |

---

## 8. For contributors — the one rule

**Never write an entity record with raw `json.dump` / `write_text` / `put_object`.** Always use
`write_json_with_lock(path, data, entity="<type>")` or `Storage.write_json` — both validate. If you
add a new entity, register it in `ENTITY_REGISTRY` (+ `ENTITY_SCHEMA_VERSIONS`) and it is guarded
automatically. A breaking schema change requires either a `register_upgrade(old, new)` or a
targeted reprocess (see `SCHEMA_VERSIONING.md`).
