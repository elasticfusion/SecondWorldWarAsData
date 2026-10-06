# Equipment Pipeline — Comprehensive Flow

End-to-end flow for equipment extraction, identity resolution, enrichment, and dedup. Every
stage lists its **module/function**, **decision points**, and **provenance/traceability
rule**. Governing principle throughout: **every fact traces to its origin source**
(`original_text` + `book` for narrative; `source` + `source_url` for enrichment), and
conflicting facts across sources are kept as separate traceable records, never silently
merged.

Entry point: `extract_equipment_from_event` (`src/extraction/equipment.py`), run per event
file in Phase 2. Dedup detection is a separate pass (`scripts/find_duplicate_equipment.py`),
wired into `ecs_entrypoint._run_dedup_detection`.

---

## Stage 0 — Load indices  (`extract_equipment_from_event` → `load_entity_indices`, `load_equipment_index`)

Builds the lookups used for linking:
- people (`_build_people_index`), people_groups (`_build_groups_index`), dates
  (`_build_dates_index`), equipment (`load_equipment_index`).
- **places**: uses the places module's own alias-aware index
  (`places._build_place_name_index`, keyed on `current_name` + every alias), falling back
  to the generic name index.

A **processed-events registry** (`_load_processed_registry`) skips already-processed event
files (idempotent re-runs).

## Stage 1 — LLM extraction  (`_extract_equipment_with_llm`)

Renders `prompts/equipment.yaml` and calls Grok (`extract_json`). The prompt teaches:
specific-named-equipment only; the origin-vs-operator split; quantity (exact vs verbatim
vague); the **assertion gate**; `image_scope`; `related_equipment` + `crew_accounts` with
mandatory `original_text`. ULIDs are repaired (`_fix_invalid_ulids`).

## Stage 2 — Per item  (`_process_equipment_item`)

For each extracted item (`EquipmentExtraction.model_validate`):

### 2a. Assertion gate (DECISION — code-enforced)
If `assertion_source` is null → **drop the item** (no mention created). A mention exists
ONLY when the source asserts presence (`narrative` or `media_narration`); ambient/stock
footage that merely shows/discusses a place is not extracted. *(Enforced in code, not just
the prompt.)*

### 2b. Entity linking
- `using_unit` → PeopleGroupID, `using_person` → PersonID (`_link_entity`).
- `supporting_units` (`_link_supporting_units`): `support_type` is the **supporting unit's
  own arm** (a P-47 wing supporting a tank is `aircraft`, not the tank's `armor`);
  resolves PeopleGroupID + supporting `EquipmentID` (`_resolve_support_equipment_id`,
  name-exact — a known gap).

### 2c. Build mention  (`_build_mention` → `_populate_mention_fields`)
Per-mention fields (distinct from the record's type-level identity):
- `operating_country`, `captured` (per-mention operator; **never** the dedup veto).
- `quantity` (exact int) + `quantity_text` (verbatim: "several" preserved, never fabricated).
- `place_name` → single **PlaceID** (`_resolve_mention_place` → `_resolve_place_id`:
  exact → whole-word containment → `SequenceMatcher ≥ 0.88`; below threshold → null, never
  guesses). A mention is ONE assertion about ONE place.
- `assertion_source`, `original_text` (retained — **traceability**).
- `DateID`/`DateMentionID` denormalized (`_link_date_to_mention`).

### 2d. Build record  (`_build_equipment_data`) + canonical identity (DECISION)
`_resolve_canonical_identity` resolves the designation to a **canonical identity** across
US/German/British naming systems via the disambiguator (Stage 3). Stamps `canonical_name`,
`identity_source`, and (gap-fill) `country_of_origin`. **This canonical name becomes the
merge key** (Stage 4), so M4 and "Sherman V" converge to one record.

### 2e. Source-recheck  (`_recheck_equipment_fields` → `equipment_source_recheck.py`)
Source-first gap-fill of missing **critical** fields — `country_of_origin` (the dedup
veto), `category`, `quantity`, `place_name` — from the mention's retained `original_text`,
BEFORE merge/dedup. Gap-fill only, provenance-stamped, fail-open.

### 2f. Record-level narrative collections (source-tracked)
- `related_equipment` (`_link_related_equipment`): narrative-sourced relationships
  (predecessor/successor/variant) to **distinct** pieces, each with `original_text`;
  resolves name→EquipmentID or **auto-creates** a minimal distinct record
  (`_autocreate_minimal_equipment`) that is itself enriched-on-identity if specific.
- `crew_accounts` (`_link_crew_accounts`): firsthand accounts; **`original_text` mandatory**
  (untraceable accounts dropped); PersonID resolved (`_resolve_person_id`, exact→fuzzy).

## Stage 3 — Canonical disambiguation  (`equipment_disambiguation.py::resolve_designation`)

Fallback chain, cheap→expensive, **cached** (one Grok call per distinct designation):

1. **exact** / **curated alias** (`config/equipment_aliases.yaml`, nickname→canonical).
2. **learned alias** (`config/equipment_aliases_learned.yaml`): auto-persisted prior
   resolutions, **first-wins** (stable across runs). Curated always overrides learned.
3. **fuzzy** (`SequenceMatcher ≥ 0.90`).
4. **Grok** canonical-lookup (`prompts/equipment_disambiguation.yaml`): designation →
   `{canonical_name, nationality_of_origin, equivalents}`. **No specs** (identity only).
   Persists to the learned store + a suggestions file for human promotion into the curated
   YAML. Flag-gated (`EQUIPMENT_DISAMBIGUATION`), fail-open to the raw name.
`identity_source` records which tier resolved it.

## Stage 4 — Merge or create  (`merge_or_create_equipment`)

**Match** (`_find_matching_equipment`), most-stable key first:
**canonical_name** → technical_identifier → exact common_name → fuzzy
(`_fuzzy_match_equipment ≥ 0.80`, incl. alternate_names).

- **Match → `_merge_into_existing`**: append the mention (deduped by
  `EventID:Sub_eventID`); merge record fields (`_merge_equipment_fields`); accumulate
  `related_equipment` (`_merge_related_equipment`) + `crew_accounts`
  (`_merge_crew_accounts`), deduped, conflicts kept; **enrichment retry** if the record was
  never successfully enriched; stamp `_last_updated`.
- **No match → `_create_new_equipment`**: assign EquipmentID + first mention; run
  **enrichment-on-identity** (Stage 5); index under technical_id, common_name, AND
  canonical_name.

## Stage 5 — Enrichment-on-identity  (`_enrich_on_identity`)

Fires once per **specific** record (DECISION: `_is_specific_identity` — a
technical_identifier, an alias-resolving nickname, or a non-generic/non-category name;
"medium tank" and "tank" are skipped). Guards:
- already `enriched` → skip (never re-enrich);
- **staleness gate** (`enrichment_gate.should_check_enrichment`, 90-day window) → skip if
  checked recently (**limit updates**).

Then `_enrich_and_add_media`:
- `_enrich_equipment_data`: Grokipedia/Wikipedia via `extract_json` (strips the ```json
  fence) → `specifications` + `variants` + urls.
- `_merge_enriched_data` → `_merge_source_tracked_reference`: `timeline`,
  `technical_evolution`, `logistics` each **stamped with `source` + `source_url`**
  (grokipedia/wikipedia).
- `_build_external_data`: `external_data` with grokipedia_url/wikipedia_url/
  additional_sources provenance.
- images: fetched (OpenSERP + wiki), vision-**TYPE**-verified, mapped to structured
  `images[]` (`_to_structured_images`) with `image_scope` default `representative`
  (books/stock illustrate the TYPE — never claimed as a specific-event photo) and
  `vision_verified`.

Finally: **diff** the revised entry (`diff_enrichment` — no-op results don't rewrite),
stamp `enrichment_status` + `enrichment_checked_at` (every check, incl. failure, so the
gate advances).

## Stage 6 — Finalize  (`_finalize_extraction` → `generate_equipment_index`)

Writes `output/equipment/index.json`; marks the event processed.

## Dedup detection (separate pass)  (`scripts/find_duplicate_equipment.py`)

`find_potential_duplicates` → `_score_pair`:
- normalize (caliber/mm/cm) + alias-expand (`equipment_aliases.yaml`);
- score name-similarity + name-contained + same-category;
- **VETOES**: leading-number mismatch (105 vs 155); **`country_of_origin` mismatch — ORIGIN
  ONLY, never operator** (British-used US Shermans / German-captured US gear stay one
  record), relaxed by the structured `captured` flag (`_any_captured`).
- `scripts/merge_equipment.py` applies confirmed merges.

---

## Provenance summary (traceability is non-negotiable)

| Data | Source kind | Trace fields |
|---|---|---|
| Mentions (quantity/place/operator) | narrative | `original_text`, `book`, `assertion_source` |
| related_equipment | narrative | `original_text` |
| crew_accounts | narrative | `original_text` + `book` (mandatory) |
| specifications / variants | enrichment | `external_data` urls |
| timeline / technical_evolution / logistics | enrichment | per-block `source` + `source_url` |
| images | enrichment | `source`, `license`, `vision_verified`, `image_scope` |
| canonical identity | resolver | `identity_source` (exact/alias/learned_alias/fuzzy/grok) |

## Known gaps (see README)

Supporting-unit equipment linking is name-exact; curated alias table lacks Sd.Kfz./British
marks (runtime Grok resolver covers the long tail + feeds suggestions); vision + OpenSERP
image path untested live; no live end-to-end run yet.
