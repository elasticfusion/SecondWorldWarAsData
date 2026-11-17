# Phase-2 Observability Audit (2026-10-19)

Comprehensive review of Phase-2 steps that LACK comprehensive observability, prioritized.
Grounded in code inspection (not impressions). "Comprehensive" here = a structured
`OBSERV <stage>.<event>` line that reports, per run/step: **attempted, produced/persisted,
skipped (with reason), errors** — enough to answer "did this step do its job?" from logs alone.

## Current coverage (baseline)

| Phase-2 step | Progress counter | Outcome count | OBSERV schema | Verdict |
|---|---|---|---|---|
| Core batch extract (events/people/places/dates/groups) | ✅ `N/M chapters done` (batch_parallel:778) | partial | ✅ `xref.seid` per type | GOOD |
| xref integrity (entity→event) | — | ✅ exact/recovered/dropped | ✅ | GOOD |
| Optional entities (weather/equipment/casualties/logistics) | — | ✅ (just added) `optional.persisted count=N` + `optional.collecting` | ✅ | GOOD (new) |
| People enrichment (Grokipedia/Wikipedia) | — | partial | ✅ `enrich.collecting` (persisted TODO) | FAIR |
| Group enrichment | — | ✅ (just added) `enrich.persisted ... fields=N` | ✅ | GOOD (new) |
| Bibliography | — | ✅ `bib.resolve in/resolved/...` | ✅ | GOOD |
| **maps** | ❌ | ❌ **no attempted/found/persisted/skipped** | ❌ | **SILENT** |
| **images** | ❌ | ❌ only captioner-init; no found/downloaded/persisted | ❌ | **SILENT** |
| **source_section** | ❌ | only `enriched N` + error-warns; no attempted/skipped/reason | ❌ | **THIN** |
| **dates / places** | — | per-call `✓ Extracted N` but no run roll-up | ❌ no OBSERV | **THIN** |
| **events** | ✅ chapters | `events.refs` only; no events/sub-events produced roll-up | partial | **THIN** |

## Prioritized gap list

### P1 — SILENT steps — ✅ DONE 2026-10-19 (PR #464): maps/images/source_section now emit
### outcome OBSERV on every exit path. Original items below.

### P1 — SILENT steps (no outcome visible at all; a zero/failure is invisible)
1. **maps** (`src/extraction/maps.py`) — logs "Starting maps extraction" then nothing about
   results. Add `OBSERV maps.extract attempted=N found=N persisted=N skipped=N errors=N`.
   (This is the class that hid the optional-entity-zero bug — a silent step can produce 0 and
   no one knows.)
2. **images** (`src/extraction/images.py`) — only logs captioner init. Add
   `OBSERV images.extract events=N images_found=N downloaded=N captioned=N skipped=N`.
3. **source_section** (`src/extraction/source_section.py`) — only `enriched %d` + error warns.
   Add `OBSERV source_section.enrich attempted=N enriched=N skipped=N errors=N` with skip reasons.

### P2 — THIN steps — ✅ DONE 2026-10-19 (PR #465): dates/places/events OBSERV *.extract
### roll-ups + places.parent_link + people enrich.persisted (enriched_now/has_bio/sources). Items below.

### P2 — THIN steps (outcome logged per-call but not aggregatable / no OBSERV schema)
4. **dates / places** — have `✓ Extracted N` per sub-event but no run-level OBSERV roll-up. Add
   `OBSERV dates.extract` / `OBSERV places.extract` with totals + geocodable/low-content skips.
5. **events** — `events.refs` exists but no `OBSERV events.extract events=N sub_events=N` roll-up
   (how many events/sub-events the chapter produced).
6. **people enrichment persisted** — `enrich.collecting` exists but no `enrich.persisted` for people
   (groups has it). Add on the retrieve/persist path once #461 bio enrichment repopulates.

### P3 — ✅ DONE 2026-10-19 (PR #466): OBSERV phase2.summary end-of-run per-type persisted
### counts (CORE zero=LOUD WARNING, optional zero=INFO) + all colon-format lines standardized to
### key=val. Covers all 13 record-producing Phase-2 entity types; 'supplemental' intentionally
### excluded (no own record dir — persists into bibliography + event sub-events). Items below.

### P3 — Consistency / standardization (not missing, but inconsistent)
7. Standardize the OBSERV schema: all should be `OBSERV <stage>.<event> key=val ...` with a
   consistent attempted/produced/skipped/errors vocabulary, so a single log query compares steps.
8. Consider a Phase-2 END-OF-RUN summary OBSERV (one line: per-entity-type persisted counts) so the
   whole run's output profile is visible without grepping — would have surfaced the optional-zero +
   people-bio-zero bugs immediately.

## Notes
- The optional-entity-zero + people-bio-zero bugs (fixed #461) were HARD to diagnose precisely
  BECAUSE these steps were silent — they produced 0 with no signal. P1/P2 close that class of blind
  spot: every step should make "I produced N (or 0)" visible, and "0 where source had content"
  loud.
- Core extraction + xref + (now) optional entities + group enrichment + bibliography are already
  well-observed; the gaps are concentrated in maps/images/source_section (P1) and the
  dates/places/events roll-ups (P2).
