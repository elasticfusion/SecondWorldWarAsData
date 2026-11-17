# External-Source Usage & Phase Split (Grokipedia / Wikipedia / OpenSERP / NOAA / NARA)

**Date:** 2026-10-06 (checked against the code 2026-11-17). The pipeline splits external enrichment **by source**:

- **Phase 2 (extraction)** — **ALL Grokipedia + Wikipedia** enrichment (descriptive text +
  images), committed natively by each extractor as it writes the entity.
- **Phase 3 (enrichment)** — **everything else**: geocoding (Nominatim/Grok coordinates), NOAA
  weather, OpenSERP (images, web pages, primary sources), NARA/Archive.org bibliography, plus **structured**
  hierarchy/org-history Grok passes (place hierarchy/names, group unit-history) that promote
  facts into spec fields — structured enrichment, not descriptive Grok/Wiki text.

---

## Grokipedia / Wikipedia — now PHASE 2 (native commit)

| Entity | Phase-2 native fn | Reuses fetch | Gated on | Commit |
|---|---|---|---|---|
| equipment | `equipment._apply_wikipedia_text_extract` (the Grokipedia URL is copied from the enrichment result in the same extractor; there is no `_apply_grokipedia_url`) | `equipment_wikipedia.search_equipment_wikipedia` + shared `grokipedia.resolve_grokipedia_url` | `wikipedia_checked_at` | native extractor save |
| people | `people._enrich_person_phase2` → `enrich_biographies.enrich_person_from_sources` | `search_grokipedia`, Wikipedia bio/image, award citations | `_is_already_enriched` / `_should_re_search` (90-day) | `_save_person_file` |
| people_groups | `people_groups` loop → `groups_wikipedia.enrich_group_from_wikipedia` | `search_group_wikipedia` + shared `grokipedia.resolve_grokipedia_url` | `wikipedia_checked_at` | `_save_group` |
| places | `places._find_or_create_place` → `enrich_places.enrich_place_from_grokipedia` + `enrich_place_from_wikipedia` | `_search_grokipedia_place`, `_fetch_place_wikipedia_full` | `grokipedia_url`/`grokipedia_checked_at`, `wikipedia_url`/`wikipedia_checked_at` | `write_json_with_lock` |
| source_section | `enrich_all_source_sections` → `derive_summary_and_operation` + `fetch_section_articles` + `fetch_section_media` | Grokipedia + Wikipedia **article** fetch (`source_section_articles`) + Wikipedia **media** (`source_section_media`) | `wikipedia_checked_at` | `_save_source_section` + first-class `images` records |

All gated (stamped-even-on-miss `*_checked_at` markers → no redundant re-fetch) and cached
(disk search cache). All commit via the entity's native save path (version-stamped).

## Phase 3 — all other external sources

| Source | Entity | Module | Gate | Note |
|---|---|---|---|---|
| Geocoding (Nominatim→hill→Grok) | places | `places_grok_geocode.cascade_geocoder` | always | coordinates + provenance; null-over-fake |
| Structured hierarchy/names Grok | places | `enrich_places._enrich_place_data` | always | promotes hierarchy/country into spec fields (NOT descriptive text) |
| Structured org/unit-history Grok | people_groups | `enrich_groups.enrich_group` | always | promotes unit_type/nationality/officers into spec fields |
| OpenSERP (images, web results, unit patch) | people, equipment (SPECIFIC scope only), people_groups, places, source_section, plus external maps when `external_maps.enabled` | `openserp_enrichment.enrich_{people,equipment,groups,places,source_sections}_with_openserp` | `supplemental_material.use_openserp` (config.yaml default true) | search-engine media; per-entity 90-day `openserp_searched` gate. Detail: `docs/current/core/PHASE3_MAP.md` |
| NOAA GHCND | weather | `noaa_weather` | `noaa_api_token` | station-observed |
| NARA / Archive.org / Gutenberg / OpenSERP / Grok LLM search | bibliography | `bibliography_resolver` | `api.nara_api_key` (NARA), `search_archive_org`, `search_gutenberg`, `llm_search`, `use_openserp` | source resolution (no LOC adapter) |

---

## Task-6 validation: every feature's native Phase-2 Grok/Wiki fn

Per-feature confirmation that each entity for which Grokipedia/Wikipedia enrichment is
meaningful now has a **native Phase-2 search-and-augment function with native commit**.

| Feature (docs/current/features) | Entity | Native P2 Grok/Wiki fn | Augment + native commit | Status |
|---|---|---|---|---|
| equipment | equipment | ✅ `_apply_wikipedia_text_extract` | ✅ | **present** |
| people | people | ✅ `enrich_person_from_sources` | ✅ `_save_person_file` | **present** |
| people_groups | people_groups | ✅ `enrich_group_from_wikipedia` | ✅ `_save_group` | **present** |
| places | places | ✅ `enrich_place_from_grokipedia` + `enrich_place_from_wikipedia` | ✅ `write_json_with_lock` | **present** |
| dates | dates | — | — | **N/A** (dates are not Grok/Wiki-enriched; derived from source) |
| weather | weather | — | — | **N/A** (NOAA/Open-Meteo = Phase 3) |
| logistics | logistics | — | — | **N/A** (internal cross-refs only) |
| casualties | casualties | — | — | **N/A** (derived from source narrative) |
| events | events | — | — | **N/A** (composed from sub-entities) |
| maps / external-maps | maps | — | — | **N/A** (grok vision map extraction, separate subsystem) |
| supplemental | supplemental | — | — | **N/A** (narrative; no Grok/Wiki descriptive pull) |
| batch_processing | — | — | — | **N/A** (infra) |

**Result:** all 4 Grok/Wiki-applicable entities have a native Phase-2 fn with native commit.
Non-applicable features are correctly N/A (their enrichment is non-Grok/Wiki or internal).

## Refactor candidates still open (flagged, not done)
- Phase-2 enrichment of places/groups runs only on the **new-record create path**; pre-existing
  records aren't back-filled by the Phase-2 pass (the `*_checked_at` gate makes this safe but
  means a one-time forward-only coverage). Consistent with the dev-env "forward fixes only" rule.
- `enrich_all_people` / `enrich_all_groups_wikipedia` / `enrich_all_equipment_wikipedia` remain
  defined for CLI/tests but are not pipeline-wired — kept intentionally; a deprecation note
  guards against accidental re-wiring into Phase 3.

## OpenSERP metrics (measurement)

Phase 3 writes a per-run OpenSERP effectiveness/health summary to
`output/metrics/openserp_metrics.json` (`openserp_enrichment.write_metrics`) and logs a one-line summary. Counters
(`_METRIC_KEYS`): entities_eligible, entities_searched, entities_enriched, entities_skipped_no_name,
queries_issued, results_returned, zero_result_queries, verify_yes, verify_no, breaker_opened, urls_fetched,
items_added, write_failed, records_unreadable (and `results_site_excluded` when the domain denylist drops
results). Derived: enrichment_rate, verify_pass_rate, zero_result_rate, search_rate (searched / eligible).
`_emit_openserp_anomalies` logs `OBSERV openserp.anomaly` when the breaker opened, when 0 of N searched
entities were enriched, when searched is under 50% of eligible (starvation), and when 50% or more of
candidates were skipped for a missing name. A zero-result response counts as a breaker failure, so
5 consecutive empty queries open the breaker for the rest of the driver run.
