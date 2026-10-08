# External-Source Usage & Phase Split (Grokipedia / Wikipedia / OpenSERP / NOAA / NARA)

**Date:** 2026-10-06. The pipeline splits external enrichment **by source**:

- **Phase 2 (extraction)** — **ALL Grokipedia + Wikipedia** enrichment (descriptive text +
  images), committed natively by each extractor as it writes the entity.
- **Phase 3 (enrichment)** — **everything else**: geocoding (Nominatim/Grok coordinates), NOAA
  weather, OpenSERP (images/academic), NARA/Archive.org bibliography, plus **structured**
  hierarchy/org-history Grok passes (place hierarchy/names, group unit-history) that promote
  facts into spec fields — structured enrichment, not descriptive Grok/Wiki text.

---

## Grokipedia / Wikipedia — now PHASE 2 (native commit)

| Entity | Phase-2 native fn | Reuses fetch | Gated on | Commit |
|---|---|---|---|---|
| equipment | `equipment._apply_wikipedia_text_extract` (+ `_apply_grokipedia_url`, + existing media) | `equipment_wikipedia.search_equipment_wikipedia` + shared `grokipedia.resolve_grokipedia_url` | `wikipedia_checked_at` | native extractor save |
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
| OpenSERP (images/academic) | people, equipment | `openserp_enrichment` | `use_openserp` | search-engine media |
| NOAA GHCND | weather | `noaa_weather` | `noaa_api_token` | station-observed |
| NARA / Archive.org / LOC / Gutenberg | bibliography | `bibliography_resolver` | config | source resolution |

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
`output/metrics/openserp_metrics.json` (and logs a one-line summary): entities_searched /
entities_enriched (+ enrichment_rate), queries_issued / results_returned / zero_result_queries
(+ zero_result_rate), verify_yes / verify_no (+ verify_pass_rate), breaker_opened, items_added.
High zero_result_rate or breaker_opened > 0 signals an OpenSERP health problem (e.g. anti-bot
blocking) at a glance — the signal that was missing when the empty-results bug had to be
diagnosed by hand.
