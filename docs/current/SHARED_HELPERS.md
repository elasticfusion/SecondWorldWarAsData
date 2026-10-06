# Shared Helpers (cross-feature)

Reusable mechanisms that **every entity feature** (people, people_groups, places, dates,
equipment, …) should use rather than re-implementing. Re-implementing these has repeatedly
caused drift and bugs (e.g. equipment deleted merged records without redirecting
references; `_should_re_search` was byte-duplicated three times). **When adding or extending
a feature, integrate these instead of writing your own.**

---

## 1. Enrichment gate — `src/enrichment/enrichment_gate.py`

Governs all Grokipedia/Wikipedia enrichment checks: **limit updates**, **stamp every
check**, **diff the revised entry**, and the shared **staleness window**.

| Function | Purpose |
|---|---|
| `should_re_search(data)` | Date-based staleness gate for `not_found` entities (`last_enrichment_search` + `enrichment.re_search_after_days`, default 90). Used by people/people_groups/places. |
| `should_check_enrichment(record, recheck_seconds=90d)` | Epoch-based staleness gate (`enrichment_checked_at`). Used by equipment. |
| `stamp_checked(record)` | Stamp `enrichment_checked_at` + `_last_updated` on EVERY check (success/no-op/failure) so the gate advances. |
| `diff_enrichment(existing, incoming)` | Return changed/new keys; empty == no-op → skip the rewrite. |
| `apply_enrichment_diff(existing, incoming)` | Apply only changed keys + stamp. |

**Rule:** never overwrite an enriched blob unconditionally — diff first; never re-check
within the staleness window.

## 2. Merge / dedup — `src/dedup/merge.py`

When records merge, references to the merged-away ID **must not dangle**, and the loser's
names **must survive as aliases** on the survivor.

| Function | Purpose |
|---|---|
| `update_event_refs(output_root, old_id, new_id, ref_key)` | **The shared reference-redirect.** Rewrites `old_id → new_id` across `*-event.json` sub-event refs (`ref_key`) + targeted ID fields in logistics/casualties/weather. Used by people, people_groups, equipment. |
| `merge_generic(...)`, `do_merge(...)`, `update_index(...)` | Generic merge + index maintenance. |

**Rule (all features):** on merge — (a) union the loser's names into the survivor's
`alternate_names`/`aliases`; (b) call `update_event_refs` to redirect references; (c) only
then delete the loser file. A record's identity is its **set** of strings (common_name +
technical/canonical + alternate_names/aliases + merged names), all routing to one survivor.

## 3. Source-recheck engine — `src/extraction/source_recheck.py`

Recover a missing REQUIRED field from the retained `original_text` (source-first) before
any external lookup. Entity-agnostic.

| Symbol | Purpose |
|---|---|
| `SourceRechecker` + `FieldRecheckSpec` | Configure per entity: which fields, how to find source text, how to extract. |
| `default_source_text(record)` | Pull `event_mentions[].original_text`. |
| `set_sourced(record, field, value, sourced_from, confidence)` | Gap-fill + provenance stamp. |

Used by `group_source_recheck.py` + `equipment_source_recheck.py`. **Rule:** source-first,
gap-fill only, provenance-stamped, fail-open.

## 4. Name→ID index — `src/utils/entity_index.py`

| Function | Purpose |
|---|---|
| `build_name_index(entity_dir, id_field, name_field)` | Build/validate a name→ID index (local). |
| `build_name_index_s3(...)` | S3 variant. |

Note: places have a richer alias-aware index (`places._build_place_name_index`, keyed on
`current_name` + aliases) — prefer it for place resolution.

## 5. Exclusions store — `src/dedup/exclusions.py`

`get_exclusion_store(entity_type, dir)` — not-a-duplicate pairs + name exclusions
(DynamoDB/local). Used by the `find_duplicate_*` scripts.

## 6. Vision (image/video) — `GrokClient.extract_json_with_image[_base64]`

**Cross-feature primitive** for all image/video analysis — used by equipment (verify an
image depicts the equipment TYPE), maps (`grok_search_maps.py`, `search_external_maps.py`),
document still-images (`image_captioner.py`), and video speaker ID
(`ingestion/video_vision.py`). The primitive is shared; the **prompt + accept/reject logic
is feature-specific** (correctly — "is this an M4?" ≠ "who is speaking?").

**Rule:** build image verification on this primitive + a feature prompt; don't reinvent the
image API call. Vision answers *what is in the image* (type), never *which specific event*
it depicts (books/stock photos — see `image_scope`).

## 7. Nationality normalization — `src/enrichment/award_sources.py::canonical_nationality`

Maps free-text nationality to a canonical ISO 3166-1 alpha-3. **Critically: the WWII USSR
is `SUN`** (not `USSR`, which isn't alpha-3, nor `RUS`, the modern Russian Federation);
`ussr`/`soviet`/`soviet union`/`russia`/`russian` all → `SUN`. Plus the Commonwealth set
(AUS/NZL/IND/ZAF/…).

**Rule:** normalize any origin/nationality before storing or comparing it (equipment does
this via `_normalize_origin`), so dedup's origin veto compares consistent codes — a Soviet
T-34 coded `SUN` on one record and `RUS`/`USSR` on another would wrongly fail to match.

---

## 8. Canonical unit key — `src/dedup/unit_key.py::derive_unit_key` / `unit_keys_match`

The single source of truth for "are these two unit designations the same unit?" Parses a
name into `(numbers, service, arm, echelon)` — NOT string similarity — so `Ninth Division`
/ `9th Division` / `9th Infantry Division` match while vetoing genuine differences. Handles:
abbreviations (`inf`/`armd`/`PIR`/`CAV`/`AD`/`PZ`/`VG`), ordinals + Roman numerals
(incl. corps >XX via a round-trip-validated parser: `LXVI`→66), Combat Commands
(`CCA/CCB/CCR`, letter-distinct, require a parent division), the US-only infantry default,
service veto (USMC/USN/USAAF), VG/grenadier unification, and an **exclusion modifier**
(`less X`/`minus X`/`(-)`) so a task-tailored complement never merges with the whole or the
excluded part.

**Reuse this** for any unit→GroupID resolution or unit dedup (group dedup
`find_duplicate_groups`, biography linking, map unit resolution all do). Do NOT re-derive
unit numbering/echelon logic locally — `find_duplicate_groups` still carries a legacy local
`ROMAN_MAP`/`_extract_numbers` that should migrate here.

## 9. Reverse map registration — `src/extraction/map_registration.py`

Links narrative entities ONTO a map as a backdrop: a map's extent (`covered_places` ×
`date_range`) back-links any entity resolved to a `(PlaceID, DateID)` inside it, association
`spatial_temporal_coverage` — the map need not name the entity. The join is the entity graph
(immune to map-OCR fuzziness). Map interior extraction + the enforced output live in
`src/schemas/map_features_output.py` (strict GeoJSON FeatureCollection; coordinates come
from the resolved PlaceID, never map pixels). See
`docs/current/features/maps/MAP_FEATURES_SCHEMA.md`.

---

## 10. Translation — `src/ingestion/translation.py` + map-vision `--translate`

Two distinct paths turn non-English sources into English-normalized, resolvable data:

- **Document path (ingestion):** `detect_language` → `translate_markdown` →
  `normalize_to_english`. Foreign-language OCR'd text documents (German KTBs, French
  reports) are detected and translated to English **before** extraction — this runs as a
  **per-page** pass in **Phase 0** (`phase0_ingest.py` → `normalize_pages_to_english`), so
  every entity extractor (events, people, …) sees English and needs no language guard of its
  own. The verbatim original is kept as a `<name>.orig.md` sidecar; `source_language` is
  stamped; places keep `historical_names` with `language`/`date_range` (e.g. Danzig→Gdańsk).
  **Scope: this guarantee is Phase-0 front-door only** — markdown placed into `output/`
  without passing through Phase 0 is not language-checked. Full spec:
  [LANGUAGE_TRANSLATION.md](dataquality/LANGUAGE_TRANSLATION.md).
- **Map-vision path:** images are not OCR text, so the vision prompt translates in place
  (`proto_map_vision.py --translate`): verbatim foreign label + English/modern equivalent
  (`title_en`, legend `meaning_en`, place `name_en`), with place resolution falling back to
  the English/modern name for exonyms (Lüttich→Liège). See
  `docs/current/features/maps/MAP_FEATURES_SCHEMA.md`.

**Rule:** preserve the verbatim original (provenance) AND the English/modern form (resolution)
— never discard the source-language text.

---

## Provenance / traceability (applies everywhere)

Every fact traces to its origin: **narrative** facts carry `original_text` (+ `book`);
**enrichment** facts carry `source` + `source_url` (or `external_data` urls); **resolved
identities** carry `identity_source`. Conflicting facts across sources are kept as separate
traceable records, never silently merged.

## Integration checklist for a new/extended feature

- [ ] Enrichment uses `enrichment_gate` (staleness + diff + stamp) — not a bespoke flag.
- [ ] Merge calls `update_event_refs` + retains loser names as aliases — never bare-deletes.
- [ ] Missing critical fields recovered via a `SourceRechecker` spec before external lookup.
- [ ] Name→ID resolution via `build_name_index` (alias/fuzzy where the feature needs it).
- [ ] Dedup consults the exclusions store.
- [ ] Unit/formation designations resolved + deduped via `unit_key` (not local string logic).
- [ ] Every stored fact carries its source (original_text/book or source/source_url).
