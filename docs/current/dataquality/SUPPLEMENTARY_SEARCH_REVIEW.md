# Supplementary Search Review — People / Groups / Equipment / Bibliography / OpenSERP / Weather

Deep per-type audit of every Phase 3 supplementary search **except geocoding**
(just overhauled). Requested 2026-10-02. Evidence-cited, read-only — findings
only, no code changed yet. Severity: CRITICAL / WARNING / SUGGESTION.

---

## Top 5 highest-value improvements (across all search types)
1. **Make all verifiers fail CLOSED** (or tag `verified=false`). `_verify_result`
   (`openserp_enrichment.py:166`) and `_verify_match`/`_verify_url_content`
   (`bibliography_resolver.py`) currently fail **open** — a Grok/HTTP blip admits
   unverified images/awards/book URLs. Bibliography NARA/archive verifiers already
   fail closed; make the rest consistent. (Low effort, high data-integrity value.)
2. **Theater/branch/nationality-aware config** for NARA RG tables + name parsing +
   keyword gates. The ETO/US+German, English, Western-name-order assumptions are
   pervasive and will systematically miss Pacific/CBI/Naval/Axis entities.
3. **Wire the `equipment_vision` prompt into the OpenSERP equipment image path**
   (`openserp_enrichment.py:230-250`) + add unit/variant/date context to
   people+equipment queries — cuts wrong-variant/wrong-person false positives.
4. **Fix Grokipedia to store extracted article text, not full HTML**
   (`enrich_biographies.py:95`) + add a query-level cache for raw OpenSERP results
   — large cache-size + repeat-run cost win.
5. **Normalize global search-state lifecycle + rate limiting:** reset
   `_openserp_down` per run, make the NARA limiter thread-safe, replace flat
   `time.sleep` with shared token buckets, don't cache NOT_FOUND on 429.

## Cross-cutting themes
- **Fail-open vs fail-closed verification is inconsistent** → silent data-integrity hole.
- **ETO/US+German/English/Western-name assumptions are pervasive** → biggest multi-theater risk; recurs in every search type.
- **Query construction is under-contextualized** (name-only) → more wrong-entity false positives + wasted Grok verify calls.
- **Flat per-call `time.sleep`** (groups 1s, equipment 1s, OpenSERP 5s, archive 5s, NARA 6s, Wikipedia 5s) is the dominant cost; several run on skips/cache hits.
- **Process-global mutable state leaks across books** (`_openserp_down` never reset; NARA `_last_call` not thread-safe).
- **Negative caching conflates transient failure with genuine miss** (NOAA 429→NOT_FOUND for 7 days).

---

## 1. People — Grokipedia + Wikipedia bios + OpenSERP images/awards
`src/extraction/enrich_biographies.py`, `src/enrichment/openserp_enrichment.py`
- **CRITICAL** Grokipedia caches the **entire raw HTML page** as the "bio" (`enrich_biographies.py:95`) → cache bloat + HTML fed to the extractor. Store extracted article text only.
- **CRITICAL (multi-theater)** Name matching assumes Western order + Latin script: `last_name=parts[-1]` + first-initial (`openserp_enrichment.py:198-220`, `enrich_biographies.py:60-75,329-347`) → silently drops Japanese (surname-first), transliterated, particled ("von Rundstedt") names.
- **WARNING** No disambiguation context in queries (`search_queries/people.yaml` is name-only) → wrong same-name person; `_verify_result` only sees the title, not unit/dates.
- **WARNING** `_verify_result` fails **open** on exception (`openserp_enrichment.py:166-168`) → unverified images/awards admitted during Grok outage.
- **WARNING** Grokipedia bare `requests.get` with no shared rate limiter (`:81`), unlike Wikipedia's cross-thread 5s lock.
- **SUGGESTION** English-only Wikipedia; add language fallback by nationality (German/Japanese/Russian WP). `_MILITARY_KEYWORDS` Euro-centric; US-only valor sources should gate on nationality==USA.

## 2. Groups / units — Wikipedia + Grok
`src/enrichment/groups_wikipedia.py`, `src/extraction/enrich_groups.py`
- **CRITICAL** `enrich_group` accepts Grok facts (commanders, dates, parent units) with **no verification** + promotes to top-level fields → fabrication risk. Verify against Wikipedia extract or tag with source/confidence.
- **WARNING** Loose keyword match (any "army"/"group"/"command"/"military") → wrong-unit articles attach. Require the designation/number in the title or Grok-verify.
- **WARNING** Per-item `time.sleep(1)` even on skips; WP path single-threaded.
- **WARNING (multi-theater)** English-only + `"{name} (World War II)"` suffix; German/Japanese unit names resolve poorly; no aliases.
- **SUGGESTION** `_ALLIANCE_MAP` small fixed set → Finnish/Romanian/Chinese/etc. units get no alliance. Two divergent "already enriched" flags with different re-search policies.

## 3. Equipment — Wikipedia + OpenSERP images + vision
`src/enrichment/equipment_wikipedia.py`, `openserp_enrichment.py`, `equipment_vision.yaml`
- **CRITICAL** `search_equipment_images` (`openserp_enrichment.py:230-250`) accepts images on a **title-only** `_verify_result`, NOT the `equipment_vision` prompt → wrong model/replica/illustration risk. The vision prompt exists but isn't wired to this path (a separate vision path exists in `src/extraction/equipment.py`, so coverage is inconsistent).
- **WARNING** Query drops variant/identifier (`"{common_name} WWII military equipment photo"`, `:234/:430`) → "Panther" vs "Panther Ausf. G", "Zero" vs "A6M5" collapse.
- **WARNING** Wikipedia suffix-guessing (`name + " tank"/" aircraft"/...`) resolves ambiguous names ("Hornet" ship vs aircraft) by suffix order, not correctness.
- **WARNING (multi-theater)** Keyword gate land/air-heavy, thin naval coverage → naval equipment rejected.
- **SUGGESTION** Per-item `time.sleep(1)`; no Wikimedia Commons category fallback.

## 4. Bibliography / sources — NARA / Archive.org / Gutenberg / OpenSERP / local
`src/enrichment/bibliography_resolver.py`, `source_retrieval.py`, `local_holdings.py`, `supplemental_search.py`
*(The most mature pipeline — citation-vs-prose gating, local-holdings short-circuit, per-citation dedup, negative caching, review queue.)*
- **CRITICAL (multi-theater)** NARA record-group ID hardcoded to ETO/US+German (`_NARA_INDICATORS`; `nara_identify.yaml` RG 331/407/338/319/218/165). Pacific (RG 38 Navy, 127 USMC, 457), CBI, MTO citations mis/under-flagged. Make config-driven per theater/branch.
- **WARNING** Module-global `_openserp_down` never reset per run → one transient error disables archive OpenSERP for every later book.
- **WARNING** Fixed `time.sleep(5/6)` serialize resolution; NARA `_last_call` limiter not thread-safe.
- **WARNING** `_verify_url_content`/`_verify_match` fail **open** → wrong book admitted on verifier error.
- **WARNING** Archive.org takes `docs[0]` + relies on (fail-open) verify; English/US-shaped title skip list won't filter German/Japanese prose stubs.
- **SUGGESTION** `isbn_lookup`/`author_death_date`/`publication_search` are LLM lookups that can emit URLs — ensure fetched+verified before storing. `local_holdings._BARE_ID` regex may false-match "B-17"/"G-2". Confirm `source_retrieval` human-disposition path isn't stranded.

## 5. OpenSERP (generic) — search/verify/breaker/ranking
`src/enrichment/openserp_enrichment.py`
- **WARNING** No result ranking — takes engine order; no preference for authoritative domains (Commons/.gov/archives) over blogs/Pinterest.
- **WARNING** `_verify_result` fails open (`:166`).
- **WARNING** Raw OpenSERP search results **not cached** at the search layer (only verify verdicts) → repeat runs re-hit OpenSERP (5s each).
- **WARNING** Flat `time.sleep(rate_limit_seconds, 5)` every call; make it a token bucket.
- **SUGGESTION** Breaker counts empty-but-reachable results as failures (can prematurely open); `_name_initial_matches` allows unparseable names through → wrong-entity verify cost.

## 6. Weather — NOAA CDO
`src/enrichment/noaa_weather.py`
*(Correct thread-safe rate-limit + good caching + 50→100km broadening.)*
- **WARNING (coverage/era)** GHCND is sparse for 1939-45 outside N.America/Europe and absent over oceans → Pacific/CBI/naval events get nothing + negative-cached. Document; consider ICOADS (marine) for naval.
- **WARNING** On HTTP 429, returns None → caller negative-caches NOT_FOUND for 7 days (transient treated as genuine miss). Don't cache NOT_FOUND on 429.
- **SUGGESTION** `station_distance_km` hardcoded None; first station in bbox chosen without a "has data for this datatype" check (wasted calls).

---

## Suggested remediation order
1. Fail-closed verifiers (#1 top-5) — quick, protects integrity everywhere.
2. NOAA/NARA transient-vs-miss + global-state lifecycle (#5) — correctness + cost.
3. Equipment vision wiring + query context (people/equipment) (#3) — false-positive reduction.
4. Grokipedia HTML fix + OpenSERP query-level cache (#4) — cost/cache.
5. Theater/nationality config (#2) — large, do alongside the multi-theater ingestion work (ties to the all-theaters-equal direction already applied to geo).
