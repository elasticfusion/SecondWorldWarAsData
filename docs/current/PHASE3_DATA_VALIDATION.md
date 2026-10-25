# Phase-3 Data Validation — Design, Audit & Plan

**Owner-driven.** Three questions, per enhancer:
1. **Do the data enhancements work as expected?** (step through each)
2. **Do we have complete observability?** (a coverage rollup + loud anomaly per enhancer)
3. **Is the produced JSON schema-compliant, are mandatory fields completed, and do we ALERT when
   not?** (every enhancer MUST persist through the central write-guard so a schema violation is
   caught + surfaced — never a silent raw write.)

Mirrors `PHASE2_DATA_VALIDATION.md`. Phase 3 is the enrichment layer; it reads Phase-2 entities and
augments them in place (idempotent/resumable). The write-guard (`write_json_with_lock` /
`locked_json` → `_validate_entity`) is the single mechanism that enforces schema + mandatory fields
+ records a block in `validation_stats`; **any enricher that raw-writes bypasses all of that.**

## Phase-3 enhancer inventory (phase3_enrich_data.py, 6 steps)
| Step | Enhancer | Source | Entity | code |
|---|---|---|---|---|
| 1/6 | people_groups Wikipedia/Grokipedia | wiki/grok | people_groups | `enrich_groups.py` |
| 2/6 | places Wikipedia/Grokipedia | wiki/grok | places | `enrich_places.py` |
| 3/6 | places geocoding (coords) | nominatim→hill→grok cascade | places | `*_geocode.py` |
| 4/6 | bibliography (ISBN/copyright/archive/NARA/death-date) | supplemental_advanced | bibliography | `supplemental_advanced.py` |
| 5/6 | OpenSERP (images/urls) | openserp | people, equipment, groups, places, source_section | `openserp_enrichment.py` |
| 6/6 | NOAA weather | noaa | weather | `noaa_weather.py` |
| (+) | `link_parent_place_ids` (hierarchy) | — | places | `places.py` |

## Audit findings (initial static pass — the 3 questions)

### Q3 — schema-guard coverage (CRITICAL; two confirmed bypasses)
- ✅ **people_groups** (`enrich_groups`) — writes via `write_json_with_lock` (guarded). 
- ✅ **places** (`enrich_places`) + **geocode** (`places_grok_geocode`) — guarded.
- ✅ **OpenSERP** (`openserp_enrichment`) — uses `write_json_with_lock` (has a few raw writes to
  verify are metrics/cache, not entities).
- 🔴 **weather** (`noaa_weather.py:327` `_enrich_weather_file`) — **RAW `f.write_text(json.dumps(...))`
  of a weather ENTITY → BYPASSES the schema guard.** A NOAA-enriched weather record is not
  schema-validated on write; a malformed/missing-mandatory record is neither caught nor alerted.
  (Contrast: `weather_central.py` Phase-2 writes DO use the guard.)
- 🔴 **bibliography** (`supplemental_advanced.py:348`) — **RAW `json.dump(data, f)` of the enriched
  bibliography ENTITY → BYPASSES the guard.** Same gap.
- ◻ **geocode helpers** (`nominatim`/`hill`) — return coords to a guarded caller; they don't write
  entities themselves (OK) — confirm.

**Fix (Q3):** route the weather + bibliography enrichment writes through `write_json_with_lock`
(entity=`weather` / `bibliography`) so schema + mandatory fields are enforced and a violation is
recorded/alerted via `validation_stats` (the `block`/`allow_flagged` path + the existing systematic-
failure SNS alert). These are also 2 of the repo's counted raw-write sites — moving them tightens
the raw-write gate.

### Q2 — observability / coverage rollup (per enhancer)
- people_groups / places / openserp: emit OBSERV / metrics (openserp has `write_metrics`).
- bibliography: only a plain `"Enriched N of M"` log — **no `OBSERV *.summary` + no 0-of-N anomaly**
  (same silent-aggregate class as the pre-fix enrichment/supplemental blind spots).
- weather: has transient OBSERV but **no coverage rollup** (weather enriched M of N, 0-of-N anomaly).
- geocode: has notes/metrics; confirm a coverage rollup (geocoded M of N, failed K).
**Fix (Q2):** add an `OBSERV <enricher>.summary attempted=N enriched=M skipped=K failed=E` + a
0-of-N / high-failure anomaly WARNING to each enhancer that lacks one (bibliography, weather,
geocode), matching `enrich.summary`/`supplemental.anomaly`.

### Q1 — do they work as expected? (requires a live Phase-3 run)
Step through each on a real run (the upcoming clean Phase-1+2+3 RoerDams run with Phase 3 UN-
suppressed): assert each enhancer actually populates its target fields on obvious candidates —
- people_groups/places: `wikipedia_url`/`grokipedia_url` + extract on e.g. "28th Infantry Division",
  "Hürtgen Forest".
- geocode: `latitude`/`longitude` populated for gazetteer places (the Phase-2 `geo_missing_coords`
  flag should clear here); hills/situational resolved via the cascade.
- bibliography: ISBN/copyright/archive/NARA fields on cited documents.
- openserp: images/resource_urls on people/equipment (gated on the OpenSERP service being up).
- weather: NOAA temp/precip on dated+placed weather mentions.
Each with the **source link captured + traceable** (the Phase-2 A4 rule, applied to Phase-3 sources).

## The validator — `scripts/validate_phase3.py` (post-Phase-3)
Extends `validate_phase2.py`'s A/B model with Phase-3-specific checks:
- **A (hard):** every enriched entity still schema-valid + version-matched (re-use the P2 A1/A2);
  an enriched record carrying an external source (wiki/grok/NARA/openserp/noaa) MUST carry the
  source LINK + be schema-clean; referential integrity still 0-dangling.
- **B (warn):** per-enhancer coverage below an expected floor (e.g. 0 places geocoded when places
  exist); geocode `geo_missing_coords` residue after Phase 3 (should be ~0, unlike pre-Phase-3).
- LOUD summary + non-zero exit on A; wired post-Phase-3 in `phase3_enrich_data.py` main().

## Plan / sequencing
1. **Fix Q3 bypasses** (weather + bibliography → write-guard) — correctness-critical, their own PR.
2. **Fill Q2 gaps** (coverage summary + anomaly for bibliography/weather/geocode).
3. **`validate_phase3.py`** + wire post-Phase-3.
4. **Q1** — run clean Phase-1+2+3 (Phase 3 un-suppressed) and step through each enhancer's output.
Each step: gate + concurrent code+security review + PR.

## Open questions
1. Confirm the A/B hard/warn split for Phase-3 (e.g. is "0 places geocoded" hard or warn — depends
   on whether the OpenSERP/geocode services are expected up in dev).
2. OpenSERP depends on an external service; its 0-coverage must be WARN (service-down) not FAIL.
3. Per-enhancer "expected floor" for the coverage anomaly (absolute 0-of-N, or a ratio?).

## REDUNDANCY FINDING — steps 1/6 (groups) + 2/6 (places) overlap Phase 2 (owner-flagged)
Phase 2 ALREADY does the Wikipedia/Grokipedia enrichment for groups + places, in place, per the
extractors' own comments: `people_groups.py:342` ("Phase 2 owns ALL Grokipedia/Wikipedia
enrichment" → `enrich_group_from_wikipedia`) and `places.py:488` (→ `enrich_place_from_grokipedia/
_wikipedia`). Phase-3 steps 1/6 + 2/6 call the SAME functions over the WHOLE corpus — so for a
record Phase 2 already enriched, Phase 3 re-scans and skips (wasted pass, not new data).
- **Not pure dead code (≠ supplemental_narrative):** the Phase-3 sweep is a legitimate BACKSTOP for
  group/place records that did NOT exist during Phase 2 and so were never enriched — provisional
  groups (`_create_provisional_group`, #536), dedup-merged, or OOB-emitted entities. Removing the
  steps outright would leave those permanently un-enriched.
- **Recommended simplification:** make steps 1/6 + 2/6 **targeted** — enrich only records with
  `enrichment_status` unset/absent (the Phase-2 misses), instead of a full-corpus re-scan. Preserves
  the backstop, removes the wasteful re-pass, and resolves the contradiction with "Phase 2 owns ALL
  enrichment." (people enrichment already moved fully to Phase 2; openserp step 5/6 is a DISTINCT
  enhancement — images/resource_urls — not redundant.)

### VERIFIED (2026-10-24): ALL group/place records are CREATED in Phase 2 → consolidate, don't backstop
Traced every people_groups/places record-CREATION site (not update):
- **groups created only at:** `people_groups._save_group` ← `extract_people_groups` (Phase 2 extractor)
  AND ← `enrich_biographies._create_provisional_group` (Phase-2 people-enrichment, #536). BOTH Phase 2.
- **places created only at:** `places._find_or_create_place` (Phase-2 extractor). Phase 2.
- `enrich_groups`(1/6), `enrich_places`(2/6), `*_geocode`(3/6), `openserp`(5/6) only UPDATE existing
  files (glob `*.json`); **dedup MERGES already-created (already-enriched) records — it creates no
  net-new un-enriched group/place.** (This corrects an earlier over-claim that dedup created groups
  Phase 2 couldn't see — it does not.)
- **Conclusion:** nothing creates a net-new group/place after Phase 2, so Phase-3 steps 1/6 + 2/6 are
  NOT a required creation-time backstop. The ONLY record that misses Phase-2 wiki/grok enrichment is
  a **provisional group**, which is minted (during Phase-2 people enrichment) with
  `enrichment_status="ambiguous"` and deliberately defers enrichment to Phase 3.
- **Decision: fully consolidate into Phase 2 + DELETE steps 1/6 + 2/6**, with one prerequisite:
  **enrich the provisional group at mint time** in `_create_provisional_group` (call the same
  wiki/grok enrichment, like people are enriched in place). After that, no record misses Phase-2
  enrichment and the Phase-3 steps are safely removable (stronger than the "targeted backstop"
  above, which is the fallback if provisional-mint enrichment proves impractical).
