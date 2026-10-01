# Phase 3 Review — what it does, code reality vs docs, and the real problems

Prepared 2026-10-01 (branch `fix/phase3-lifecycle`) to scope Phase-3 work. Combines
a full documentation sweep with code verification, because several docs are stale.

## 1. What Phase 3 actually does (verified in `phase3_enrich_data.py:main`)

Phase 3 is **enrichment** — it runs AFTER the Phase-2→3 dedup gate and enriches
extracted entities with external data. Verified step list in `main()`:

1. **People** — biographical enrichment (`enrich_people_data` → `src/extraction/enrich_biographies.py`): Grokipedia → Wikipedia search, Grok structured extraction (birth/death, ranks, units, awards, family, source_urls), merges into `biographical_profile`, follows references, validates source URLs. **Production.**
2. **People groups** — org history + command structure (`enrich_groups_data`). **Production.**
3. **Places** — `enrich_all_places` (step 3/6). **Geocoding IS implemented and wired** (contradicts the steering "lat/long=0.0, no geocoding" note): `nominatim_geocode.py`, `places_grok_geocode.py`, `hill_geocode.py`, `situational_geocode.py`, `elevation_verify.py`, `places_geo.py` (bounding box + map URLs). **Production.**
4. **Bibliography** — citation resolution + source verification (`bibliography_resolver.py`, `source_retrieval.py`): routes by doc type (NARA Record Group → OpenSERP → Archive.org; books → Archive.org/Gutenberg), ISBN/copyright/archive-URL verification. **Production, but no human-disposition UI for the review queue.**
5. **OpenSERP enrichment** — portraits/papers/photos/primary sources (circuit breaker after 5 empty).
6. **Weather** — NOAA CDO observed-station data supplementing Open-Meteo. **Experimental.**

External sources: Grokipedia, Wikipedia, OpenSERP, Archive.org, Gutenberg, NARA Catalog, NOAA CDO, Nominatim/Grok geocoding.

## 2. Config reality (actual `config.yaml`, not the stale archive defaults)

- `batch.phase3: false` → **Phase 3 runs LIVE calls** (no batch poller dependency; unlike Phase 2).
- `enrichment.re_search_after_days: 0` → **re-searches everything every run** (thorough, expensive).
- `supplemental_material`: `use_openserp/verify_archive_urls/extract_isbn/determine_copyright` all **true**.
- `equipment.enable_enrichment: true` (but equipment is Experimental).
- `concurrency.max_enrichment_workers` threads per entity type.
- `concurrency.multi_doc.enabled` — the doc-lifecycle switch (see §4).

## 3. Dedup gate (between Phase 2 and Phase 3) — the thing that actually gates reaching Phase 3

Runs after Phase 2: reclassify military units places→groups, clean indexes, migrate
exclusions to DynamoDB, run 4 dedup scripts (people/groups/places/equipment).
- **Auto-proceed** to Phase 3 only if zero duplicate groups pending; otherwise **block** for human review (web UI: merge/skip/reclassify; decisions persist in DynamoDB; snapshot+undo).
- Incremental dedup (only new files), cross-book dedup (full inventory download in AWS).

## 4. THE blockers we hit (why nothing reached Phase 3) — current, code-verified

These are the problems *this branch* exists to finish. Two are already fixed, one is the live fix:
- **(FIXED, merged to main) Dedup `'int' object is not iterable`** — `_auto_merge_entity_type` read the int count key instead of the `duplicates` list; failed dedup for EVERY doc → gate blocked everything. Fixed `15e3397`.
- **(FIXED, merged) `MULTI_DOC_ENABLED` missing on phase task defs** — lifecycle guards early-returned. Fixed `d8489c8`.
- **(FIX ON THIS BRANCH, `b06e03f`) `_multi_doc_enabled()` split-brain** — the `_post_process` branch selector read `config.yaml` while the guards read the env var, so it chose the serial branch and NEVER advanced/off-ramped the doc (B401 proved: clean dedup, gate block, stayed phase1). Now honors the env var (repo-tracked via CFN param). **Not yet deployed/proven live.**

## 5. Real open problems (candidates for Phase-3 work), ranked by evidence

**A. Prove the lifecycle end-to-end (immediate, this branch).** Deploy `b06e03f`, re-run Patton → confirm gate-block → `needs-review` (correct, Patton has real dup groups), and a zero-dup doc → auto-proceed → `extracted/phase3` → enrich → `done`. Until observed once, Phase 3 is unproven live.

**B. Dedup quality — index normalization + name hallucination (high, recurring-duplicate root cause).** Archive DEDUP_ANALYSIS flagged `_normalize_name` as too weak and Grok name-expansion ("Collins"→"J. Lawton Collins", "Sherman"→"M4 Sherman") spawning duplicate files. CODE CHECK: `normalize_name`/`normalize_name_ascii` in `text_utils.py` now do case+punctuation+ASCII folding (stronger than the archived `strip().lower()`), and name-based DynamoDB exclusions + basename processed-events shipped — so several archive bugs are REMEDIATED. Still unconfirmed: `source_name`/`identified_as` to curb hallucinated-canonical variants. **Verify current duplicate rates before investing.**

**C. Bibliography stub cleanup + human-disposition UI (medium).** Endnote fragments ("ibid", "1", "26") don't resolve to real sources; `review_queue.json` is written but no UI works it; `source_retrieval.py` staged but intentionally not auto-wired.

**D. Group dedup LLM verification is disabled (medium).** Confidence-threshold only; LLM verify turned off.

**E. Equipment dedup exact-match only (medium, equipment is Experimental).** "Sherman" ≠ "M4 Sherman", no fuzzy/alias.

**F. Image captioning (low/opportunity).** Empty `description` fields; vision captioning not implemented in Phase 3.

**G. Hygiene (low).** `phase3_enrich_data.py:main` complexity D(23); `query_unenriched` full-table scan (needs GSI); NAT-between-phases race.

## 6. Stale docs to correct (found during this review)

- Steering `project-overview.md` says places lat/long=0.0 + no geocoding → **stale**; geocoding is implemented and wired in Phase 3 step 3.
- `PHASE3_COMPLETE.md` shows supplemental flags default `false` → actual config has them `true`.
- Archive `DEDUP_ANALYSIS*` (2026-05-23): several "critical/high" bugs (ULID exclusion keys, absolute-path processed-events, O(n²) full-corpus) appear **remediated** in current code; treat as historical.

## Recommendation
Start with **A** (prove the lifecycle — it's the actual Phase-3 blocker and nearly done), then
**measure real duplicate/quality rates** on a clean end-to-end run before committing to B–F,
so we invest in the problems that are actually hurting the corpus rather than the archived ones
that were already fixed.

## UPDATE 2026-10-01 — (A) PROVEN + retrieve-path fixes

**Lifecycle PROVEN end-to-end (live).** Patton phase2 retrieve on the fixed image reached:
`reclassify → run dedup → Auto-merged 21 (no 'int' error) → Dedup gate: BLOCK (duplicate
groups pending) → doc lifecycle: Patton → needs-review → exit 0`. Doc confirmed
`status=needs-review` (was stuck at phase1 all session). This validates all three fixes
together: dedup `int` (`15e3397`), MULTI_DOC_ENABLED task-def env (`d8489c8`), and the
`_multi_doc_enabled` env-var reconciliation (`b06e03f`). `needs-review` is the correct
terminal state — Patton has real duplicate groups, so the gate blocks rather than auto-proceeds.

**Retrieve-path bugs found + fixed while proving it:**
- Optional-entity markers not downloaded on retrieve → every retrieve re-extracted
  weather/equipment/logistics/casualties/supplemental live. Fixed: download
  `.processed_events.json` markers (`9ac3f75`). Verified live: logistics/casualties/equipment
  skipped on the proof run.
- **Supplemental non-convergence (the worst):** `_extract_supplemental` was the ONLY optional
  extractor missing both the `_is_processed` skip guard and `_mark_processed`, AND
  `config.yaml` had `reprocess_types: [supplemental]` forcing re-extract. Together it re-fetched
  every endnote (slow ibiblio HTTP) on every retrieve and never finished — a non-terminating
  sink. Fixed: added the skip-guard + mark pattern (matches siblings) and removed supplemental
  from `reprocess_types`.

**Still open (follow-ups, NOT fixed):**
- **Core-type live re-extraction in retrieve.** dates/places/people/people_groups re-extract
  live on every `--retrieve-only` (no processed-events marker for core types; they rely on
  per-event file existence / cache which the retrieve re-run doesn't short-circuit). This is the
  biggest remaining retrieve cost/latency driver (~30+ min of the proof run). Needs a core-type
  skip mechanism analogous to the optional markers.
- The retrieve path re-runs the FULL phase2 rather than just ingesting batch events + running
  downstream — a design-level simplification worth considering.
