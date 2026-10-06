# Phase 3 Enrichment — Review Findings

**Date:** 2026-10-06
**Reviewed against:** `phase3_enrich_data.py` (orchestrator) + `src/enrichment/*`,
`src/extraction/enrich_*`, and every `docs/current/features/<entity>/` doc.
**Authoritative process doc produced:** [core/PHASE3_ENRICHMENT.md](core/PHASE3_ENRICHMENT.md).

---

## Process per data type (as coded)

| Step | Entity | Process | Gate |
|---|---|---|---|
| 1/6 | People | Grokipedia + Wikipedia biography; references; portrait + award-citation sourcing | always |
| 2/6 | People Groups | Unit-history / external data | always |
| 3/6 | Places | hierarchy/names + `link_parent_place_ids` + geocoding cascade (Nominatim→hill→Grok) → coordinates | always |
| 4/6 | Bibliography | ISBN/copyright/archive URLs + source resolution (NARA, Archive.org, LOC, Gutenberg) | always |
| 4b | Equipment | Wikipedia images + extracts | `equipment.enabled` |
| 4c | People Groups | Wikipedia images + extracts (2nd pass) | always |
| 5/6 | People + Equipment | OpenSERP images / academic | `supplemental_material.use_openserp` |
| 6/6 | Weather | NOAA GHCND station-observed (supplements Open-Meteo) | `api.noaa_api_token` |

**No Phase-3 enrichment (by design):** Dates, Logistics, Casualties, Maps, Supplemental
narrative, Events, map_features.

---

## Findings

1. **Default run is narrow.** Only people / people_groups / places / bibliography run
   unconditionally. Equipment (4b), OpenSERP (5), and NOAA weather (6) are config/token
   gated — a plain local run does none of them. (Previously not clear in docs.)

2. **Reliability model is solid and was undocumented.** `_run_step` isolates each source:
   a failure is logged + recorded in `source_stats`, written to
   `output/.phase_results.json` (`source_stats` + `errored_sources`), and surfaced to
   email + Slack via the completion notification — never swallowed, never aborts the rest
   of Phase 3. Now documented.

3. **Batch re-run was undocumented.** `--batch` collects requests, submits to the xAI Batch
   API (~50% off), waits, then re-runs people/groups/places/bibliography from cache.

4. **Places geocoding is the only coordinate source.** `enrich_all_places` sets
   hierarchy/names but NOT coordinates; the cascade (Nominatim→hill→Grok) wired in at step
   3c writes WGS84 + provenance, low-confidence flagged not fabricated. The maps overlay
   inherits coordinates from this via resolved PlaceID.

## Gaps identified

- **map_features has no Phase-3 wiring** — map-interior extraction + reverse-registration
  (`src/extraction/map_registration.py`) are prototype-level, not in `phase3_enrich_data.py`.
  Deferred until maps productionize (gated map step: extract → resolve → register).
- **Casualties has no feature-doc directory** (`docs/current/features/casualties/` absent) —
  the only entity with no feature README. **← addressing next.**
- Casualties, Dates, Logistics, Maps, Events correctly have no enrichment, but their docs
  were silent on it (now state "None by design").

## Documentation actions taken

- Created `core/PHASE3_ENRICHMENT.md` (authoritative overview).
- Expanded `core/PIPELINE.md` Phase-3 section + link.
- Added a "Phase 3 Enrichment" section to all 9 feature docs (process or "None by design"),
  each linking the overview.
