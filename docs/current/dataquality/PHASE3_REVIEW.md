# Phase 3 (Enrichment) Review — Findings

Rigorous, evidence-based audit of the enrichment phase (requested 2026-10-02 — the
owner suspected it "isn't as solid as it should be"). Scope: `phase3_enrich_data.py`,
`src/enrichment/*`, `src/extraction/enrich_*`, `lambda_handlers/phase3_handler.py`.
Every finding cites file:line. **Read-only review — no code changed yet.**

---

## Verdict

The owner's instinct is correct. Phase 3 is a set of individually-reasonable
enrichers wired together loosely, **with one whole subsystem (geocoding) not wired
in at all** — which is the root cause of the long-standing "places have
lat/long = 0.0, empty country" gap. It works on the happy path but is fragile and
under-instrumented: silent failure-swallowing, no partial-success reporting,
module-global state unsafe under the project's own multi-doc concurrency, and
transient errors cached as durable `not_found` (suppressing retries for 90 days).

## Fix first (top 3)
1. **C1 — Wire the geocoding cascade into `main()`. ✅ DONE (PR #217)** — cascade
   (Nominatim→hill→Grok) now writes coordinates; +4 regression tests.
2. **C2 + C3 — Stop swallowing failures; report structured per-source stats.**
   Next up. Without this, "complete" doesn't mean "enriched."
3. **M3 + H3 — Stop caching transient errors as `not_found`; make breaker/
   rate-limiter/image-cache globals per-run + thread-safe.** Restores idempotent
   re-runs under concurrency.

---

## CRITICAL

**C1. Geocoding cascade is dead code — places never get coordinates. ✅ FIXED (PR #217).**
`phase3_enrich_data.py:242-253` calls `enrich_all_places` (`src/extraction/enrich_places.py:401-440`), which only sets hierarchy/historical_names/wikipedia/images — **never `coordinates`**. The real geocoders (`places_grok_geocode.geocode_places_dir`/`cascade_geocoder`, `nominatim_geocode`, `situational_geocode`, `hill_geocode`, `elevation_verify`, offline `places_geo.enrich_places_dir`) have **zero production callers** (grep: only tests/docs). → Wire `geocode_places_dir(..., geocoder=cascade_geocoder(...))` into `main()`; add a smoke test asserting a known town gets non-zero coordinates.

**C2. Silent exception swallowing violates the no-silent-failure principle.**
Bare `except Exception: pass` in `phase3_enrich_data.py:48-49` (`_notify_enrichment_started`), `:72-73` (`_update_lock_status`); plus `enrich_places._fetch_place_wikipedia_full`/`_fetch_image_license`/`_search_grokipedia_place`, `equipment_wikipedia._fetch_license`, `groups_wikipedia._fetch_license`, `noaa_weather._get` (→debug). API/lock/license failures vanish with no WARNING + no metric; the run still reports "complete." → Downgrade to `logger.warning`, count per-source failures into `.phase_results.json`.

**C3. No completion signal reflects partial enrichment.**
`phase3_enrich_data.py:328-375` writes `.phase_results.json` = only `{enriched, entity_counts}` — a heterogeneous sum; failures/API-errors/breaker-trips/not-found are discarded. A run where every NOAA call 429'd looks identical to a clean run. → Each enricher returns attempted/enriched/not_found/errors/skipped (geocoders already produce `GeocodeRunReport`); aggregate + alert on non-trivial error rate.

## HIGH

**H1. Divergent, dormant per-entity Lambda path will double-enrich/drift.**
`lambda_handlers/phase3_handler.py:22` `_enrich_entity` (`:92-140`) is a second enrichment entry point referenced only by itself + a test (not in CFN). If ever re-enabled by an S3 notification it races the ECS task writing the same `output/{type}/*.json` (last-writer-wins, no lock) and bypasses the cascade/bibliography-resolver. → Delete it, or make it the single shared source of truth with a per-entity lock.

**H2. OpenSERP marks entities "searched" even on empty/failed search.**
`openserp_enrichment.py:505-583` buffers all candidates in memory (grows with corpus); the equipment path sets `openserp_searched=True` even when nothing found (`:573-575`, `:629-631`), so a transient outage suppresses retries for 90 days. → Only stamp `openserp_searched_at` on a search that actually ran AND returned; stream per-file; honor the breaker mid-run.

**H3. Global mutable state unsafe under concurrency.**
`openserp_enrichment.py:27-29` `_consecutive_failures`/`_circuit_open` (never reset, no lock), `noaa_weather.py:38-39` non-atomic rate limiter, `equipment_wikipedia`/`groups_wikipedia`/`enrich_biographies` module image-caches — all mutated under `ThreadPoolExecutor(max_workers=6)` and across runs in one process. An opened breaker poisons later books in the same task. → Encapsulate per-run + lock; reset between runs.

**H4. Unconditional `time.sleep(1)` per entity in Wikipedia enrichers.**
`equipment_wikipedia.py:~210` + `groups_wikipedia.py:~185` sleep 1s per file **even on cache hit/skip**, single-threaded. Tens of thousands of files → hours of pure sleep. → Move the sleep inside the branch that makes a real HTTP call.

**H5. Batch-mode re-run silently differs from the non-batch path.**
`phase3_enrich_data.py:280-317` re-run omits `max_workers=` (falls to default 6, ignoring `max_enrichment_workers`) AND omits the equipment-wiki/groups-wiki/OpenSERP/NOAA steps entirely. Results depend on `--batch`. → Factor the enrichment sequence into one function both passes call.

## MEDIUM

**M1.** `_needs_geocode`/`_coords` use `0.0` as a magic "empty" sentinel (`places_grok_geocode.py:184-188`, `places_geo.py:140-146`, `noaa_weather.py:188-193`) — fine for ETO but implicit; masks C1. → Use an explicit presence/`geocode_source` check (moot once C1 lands).

**M2.** Bibliography resolver dedup is per-run only + not concurrency-safe; holdings index rebuilt by full `rglob` each run (`local_holdings.py:63-73`); NARA/OpenSERP globals race under multi-doc. → Persist dedup in cache, build holdings index once, guard globals.

**M3.** `enrich_place`/`enrich_group` write `not_found` + `last_enrichment_search` on **transient** Grok/Wikipedia errors (`enrich_places.py:385-387`, `enrich_groups.py:148-166`), so the 90-day guard suppresses legitimate retries. → Distinguish "genuinely not found" from "search errored"; only stamp on a clean negative.

**M4.** Non-atomic writes: `openserp_enrichment`/`equipment_wikipedia`/`groups_wikipedia`/`noaa_weather` use plain `write_text` (no temp+rename+lock) unlike `enrich_places`' `write_json_with_lock` — crash mid-write truncates entity JSON under concurrency. → Route all entity writes through `write_json_with_lock`.

**M5.** OpenSERP circuit breaker counts legitimate empty (HTTP-200) results as failures (`openserp_enrichment.py:80-104`) — 5 obscure-name misses disable OpenSERP for the whole process. → Only count transport/HTTP errors.

## LOW
- **L1.** `max_items` means different things per enricher (files-considered vs items-enriched vs candidates). Standardize.
- **L2.** `_name_initial_matches` pre-filter can reject valid name variants (minor recall loss).
- **L3.** `enrich_places`/`equipment_wikipedia`/`groups_wikipedia` call `requests.get` directly instead of the pooled `http_pool.get_session()` used elsewhere.

---

## Suggested remediation order
C1 (wire geocoding) → C2+C3 (failure visibility) → M3+H3 (idempotent retries +
concurrency-safe state) → H1/H2/M4 (dedup the entry point, fix search-stamping,
atomic writes) → H4/H5 (perf + batch/non-batch parity) → M/L cleanups. C1 is the
single highest-leverage fix and the direct answer to the owner's concern.
