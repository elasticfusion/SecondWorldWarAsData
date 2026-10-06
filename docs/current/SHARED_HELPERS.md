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
- [ ] Every stored fact carries its source (original_text/book or source/source_url).
