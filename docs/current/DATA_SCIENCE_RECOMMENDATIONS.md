# Data Science Review & Recommendations

**Date:** 2026-05-24  
**Scope:** Full output analysis of 26,000+ entity files across 11 types, extraction pipeline code, prompts, and schema definitions.  
**Books Processed:** 3 (Cross-Channel Attack, Breakout and Pursuit, The Lorraine Campaign)

---

## Executive Summary

The pipeline successfully extracts structured entities from WWII historical texts at scale. However, several data quality issues significantly reduce the dataset's utility for downstream analysis, visualization, and cross-referencing. The most impactful problems are:

1. **Weather duplication** — 1,320 files where ~100 unique observations exist (up to 21 duplicates per date+location)
2. **Broken cross-references** — 81% of weather PlaceIDs don't resolve to actual place entities
3. **Missing identity data** — 193 people files lack both name and PersonID fields
4. **Low enrichment yield** — 94% of people have no biographical profile
5. **Unlinked casualties** — 86% of casualty records have null PeopleGroupID

These issues are fixable with targeted pipeline changes. Below are prioritized recommendations.

---

## Data Quality Findings

### Entity Counts

| Entity Type | Files | Notes |
|---|---|---|
| Weather | 1,320 | ~90% are duplicates |
| Places | 2,091 | All have coordinates |
| People | 1,038 | 193 missing name/PersonID |
| People Groups | 1,457 | — |
| Dates | 4,698 | 98% have date_precision field |
| Equipment | 596 | 6 files with non-standard categories |
| Logistics | 4,056 | Severity skews high/critical (79%) |
| Casualties | 6,472 | 86% missing PeopleGroupID |
| Bibliography | 7,647 | — |
| Maps | 55 | — |
| Event Files | 459 | Across 2 books (Lorraine has 0) |

### Critical Issues

#### 1. Weather: Massive Duplication (Severity: Critical)

The weather extraction creates one output file per text mention, not per unique weather observation. The same date+location combination appears up to 21 times:

```
21× 19441003_Fort_Driant
20× 19440927_Fort_Driant
19× 19441008_Arraye-et-Han
19× 19441004_Sivry
18× 19441115_Guébling
```

**Root cause:** The weather prompt extracts per sub-event. When multiple chapters reference the same weather event, each extraction creates a new file. Unlike people/places/equipment, weather has no dedup script.

**Impact:** Inflates entity count ~13×. Any analysis counting weather events or joining on WeatherID will produce incorrect results. The 1,320 files likely represent ~100-150 unique weather observations.

#### 2. Weather: 81% of PlaceIDs Don't Resolve (Severity: Critical)

Of weather files that have a PlaceID, 81% reference IDs that don't exist in `output/places/`. Additionally, 69% of weather files (915/1,319) have no PlaceID at all.

**Root cause:** The weather prompt says "link to PlaceMentionID" but the LLM generates new ULIDs rather than matching to existing place entities. The prompt provides `{places_section}` but the LLM doesn't reliably use those IDs.

**Impact:** Weather data cannot be joined to the places graph. Geographic analysis of weather patterns is impossible without manual reconciliation.

#### 3. People: 193 Files Missing Name and PersonID (Severity: High)

18.6% of people files have no `name` or `PersonID` field. These appear to be extraction artifacts where the LLM returned partial data (e.g., just rank and nationality from a mention like "the Colonel").

**Example:** `output/people/abrams.json` — has rank "Colonel" and nationality "USA" but no PersonID field. The filename suggests this is Creighton Abrams but the extraction didn't capture the full name.

#### 4. People: 94% Lack Biographical Profiles (Severity: Medium)

Only 62 of 1,038 people have enrichment_status "enriched". The remaining 783 are "not_found" and 193 have no enrichment fields at all. This is expected for minor figures, but notable commanders (e.g., Adolf Hitler) also lack biographical profiles.

**Root cause:** The enrichment pipeline searches for biographical data but has a low hit rate. The 90-day re-search window means failed searches aren't retried frequently.

#### 5. Casualties: 86% Missing PeopleGroupID (Severity: High)

5,577 of 6,472 casualty records have `"PeopleGroupID": null` in their impacted_organizations. The organization name is present (e.g., "British forces") but not linked to the people_groups entity.

**Root cause:** The casualties prompt provides `{entity_context}` for cross-referencing but doesn't explicitly instruct the LLM to match organization names to existing GroupIDs. The LLM extracts the name but leaves the ID null.

#### 6. Equipment: Inconsistent Category Values (Severity: Low)

Six files use non-standard category values:
- `"Medium Tank"` (should be `"armor"`)
- `"infantry"` (should be `"infantry_weapons"`)

The schema defines an enum (`armor|aircraft|naval|artillery|infantry_weapons|communications|vehicles|uniforms|other`) but it's not enforced at extraction time.

#### 7. Logistics: Severity Distribution Skew (Severity: Medium)

| Severity | Count | % |
|---|---|---|
| high | 2,192 | 54% |
| critical | 1,018 | 25% |
| medium | 808 | 20% |
| low | 37 | 1% |

79% of logistics events are rated high or critical. This likely reflects prompt bias — military history texts emphasize significant supply problems, and the LLM defaults to high severity without calibration guidance.

#### 8. Logistics: Schema Drift Between Prompt and Output (Severity: Medium)

The prompt defines `type` values as: `supply_shortage, transportation_disruption, capacity_constraint, distribution_failure, production_delay`

The output contains: `supply_shortage, supply_excess, delivery_delay, transport_disruption`

These don't match. The extraction code or a later pipeline stage is mapping to different values than the prompt specifies.

#### 9. Schema Inconsistencies (Severity: Low, Documented)

Already documented in SCHEMA_REFERENCE.md:
- `Sub-eventID` (hyphen) vs `Sub_eventID` (underscore) across entity types
- `mentions` vs `event_mentions` array naming
- `EventMentionID` vs `MentionID` in logistics

These are cosmetic but complicate downstream queries.

---

## Recommendations

### Priority 1: Fix Data Integrity (Immediate)

#### R1. Implement Weather Deduplication

Create a dedup script for weather (similar to existing `find_duplicate_places.py`):
- Key on `(date, normalized_place_name)` — fuzzy match on place name
- Merge `event_mentions` arrays from duplicates into a single canonical file
- Prefer the file with `source_type: "hybrid"` (has API data) over `"extracted"`
- Expected reduction: 1,320 → ~100-150 files

#### R2. Post-Processing Pass to Resolve PlaceIDs in Weather

After weather extraction, run a reconciliation step:
1. For each weather file, fuzzy-match `location.place_name` against `output/places/*.json` names
2. Use coordinates (lat/lon) as a secondary match signal — weather files have coordinates, places have coordinates
3. Set `PlaceID` to the matched place entity's ID
4. Log unresolved cases for manual review

#### R3. Clean Up People Without Name/PersonID

For the 193 people files missing identity:
- If filename contains a recognizable name (e.g., "abrams.json"), populate `name` from filename and generate a PersonID
- If filename is ambiguous, merge event_mentions into the most likely matching person entity
- Delete truly unresolvable stubs

#### R4. Link Casualties to PeopleGroupIDs

Post-extraction reconciliation:
1. For each casualty with `impacted_organizations[].PeopleGroupID == null`:
2. Fuzzy-match `name` against `output/people_groups/*.json` group names
3. Set the GroupID on match
4. Expected resolution: 70-80% of the 5,577 unlinked records

### Priority 2: Improve Extraction Quality (Next Sprint)

#### R5. Inject Entity IDs Into Prompts

The biggest cross-reference failure is that prompts tell the LLM to "link to existing entities" but don't provide the actual ID lookup table in a format the LLM can use reliably.

**For weather:** Include a compact lookup in the prompt:
```
Available places (use these PlaceIDs):
- "Normandy" → 01KHYP2M4N...
- "Caen" → 01KHYP2N5P...
```

**For casualties:** Include group name → GroupID mapping:
```
Available organizations (use these PeopleGroupIDs):
- "1st Infantry Division" → 01KPSH9ERD...
- "VII Corps" → 01KPSF7Y2W...
```

This is the single highest-impact prompt change. The LLM can match strings to IDs when given an explicit table.

#### R6. Add Extraction-Time Validation

Before writing entity files to disk, validate:
- Required fields present (name, PersonID for people; category in enum for equipment)
- Referenced IDs exist in the output directory
- No duplicate files for the same logical entity (weather date+location check)

Reject or flag invalid extractions rather than writing broken files.

#### R7. Calibrate Severity in Logistics Prompt

Add calibration examples to the logistics prompt:
```
Severity guide:
- critical: Operations halted entirely (e.g., no fuel for an army)
- high: Significant degradation (e.g., rationing ammunition)
- medium: Noticeable constraint (e.g., delayed resupply by 1-2 days)
- low: Minor inconvenience (e.g., substituting one supply type for another)
```

#### R8. Normalize Equipment Categories

Add a post-extraction normalization step that maps non-standard values:
- `"Medium Tank"` → `"armor"`
- `"infantry"` → `"infantry_weapons"`

Or enforce the enum in the extraction validator (R6).

### Priority 3: Structural Improvements (Future)

#### R9. Unify Schema Naming Conventions

Pick one convention and migrate:
- `Sub_eventID` everywhere (underscore) — matches Python conventions
- `event_mentions` everywhere — most common pattern
- `MentionID` everywhere — most common pattern

Write a migration script that updates all existing files.

#### R10. Add Data Quality Metrics Dashboard

Create an automated quality report that runs after each extraction batch:
- Cross-reference resolution rate per entity type
- Null field rates for required fields
- Duplicate detection counts
- Entity count trends over time

Store in `output/metrics/quality_report.json`.

#### R11. Implement Incremental Enrichment Strategy

For the 783 people with `enrichment_status: "not_found"`:
- Prioritize by mention count (people referenced in 5+ sub-events are more important)
- Try alternative search strategies (rank + unit + date range instead of just name)
- For historical figures with non-English names, search in the original language

#### R12. Weather: Deduplicate at Extraction Time

Instead of extracting per-mention and deduplicating later, modify the weather pipeline to:
1. Check if a file for `(date, place_name)` already exists before extraction
2. If it exists, append the new event_mention to the existing file
3. Only create new files for genuinely new weather observations

This prevents the duplication problem at source rather than fixing it after the fact.

---

## Data Usability Assessment

| Use Case | Current Readiness | Blocker |
|---|---|---|
| Timeline visualization | ✅ Ready | Dates are clean, 98% have precision |
| Geographic mapping | ⚠️ Partial | Places are good; weather/casualties lack place links |
| Personnel network analysis | ❌ Blocked | 94% lack biographical data; 19% lack identity |
| Supply chain analysis | ⚠️ Partial | Severity skew and schema drift reduce reliability |
| Casualty analysis | ⚠️ Partial | 86% can't be linked to specific units |
| Weather-operations correlation | ❌ Blocked | Duplication and broken PlaceIDs |
| Equipment usage patterns | ✅ Ready | Minor category cleanup needed |
| Citation/bibliography graph | ✅ Ready | 7,647 entries, well-structured |

---

## Quick Wins (< 1 day each)

1. **Fix 6 equipment category values** — simple find/replace script
2. **Populate name from filename** for 193 people stubs — regex on filename
3. **Generate weather duplicate report** — group by date prefix, output merge candidates
4. **Add severity calibration** to logistics prompt — text change only
5. **Reconcile logistics type enum** — map output values to match prompt or vice versa

---

## Conclusion

The pipeline architecture is sound — the phase-based approach (parse → extract → enrich → import) with ULID cross-referencing is well-designed for this domain. The primary issues are:

1. **Missing dedup for weather** (architectural gap)
2. **Prompts don't provide entity ID lookup tables** (prompt engineering gap)
3. **No extraction-time validation** (quality gate gap)

Fixing these three root causes would resolve ~80% of the data quality issues identified above. The remaining 20% (enrichment yield, severity calibration) are optimization problems that improve gradually with iteration.
