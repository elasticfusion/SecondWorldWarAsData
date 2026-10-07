# Phase 3: Enrichment — Authoritative Overview

**Source of truth:** `phase3_enrich_data.py` (orchestrator) + `src/enrichment/*` and the
`src/extraction/enrich_*` modules. This doc describes what actually runs; per-entity feature
docs point here.

Phase 3 takes the extracted + deduplicated entities (`output/<entity>/*.json`) and augments
them with data from external sources. It runs after the dedup review gate; in AWS mode it is
auto-triggered when Phase 2 finds no duplicates, and reads the DynamoDB `manifest#phase2` to
enrich only changed files.

**Enrichment is split by SOURCE (not by entity):**
- **Phase 2** owns **all Grokipedia + Wikipedia** enrichment (descriptive text + images),
  committed natively by each extractor as it writes the entity — people (biography, portrait,
  award citations), people_groups (Wikipedia text + images), places (Grokipedia/Wikipedia
  descriptive text + image), equipment (Wikipedia extract + images). See each feature doc.
- **Phase 3** owns **every other external source**: geocoding (Nominatim/Grok coordinates),
  NOAA weather, OpenSERP (images / academic search), NARA / Archive.org bibliography
  resolution, plus the **structured** hierarchy/org-history Grok passes that promote facts into
  spec fields (place hierarchy/names; group unit-history) — these are structured-field
  enrichment, not descriptive Grokipedia/Wikipedia text, so they stay in Phase 3.

---

## Step order + per-entity process

Steps run in this fixed order. Several are **conditional** (config/token gated) — a default
local run performs only the unconditional ones (groups, places, bibliography).

| Step | Entity | Process | Module | Gate |
|---|---|---|---|---|
| 1/6 | **People Groups** | Structured unit/org history (promoted into spec fields) | `enrich_groups.enrich_all_groups` | always |
| 2/6 | **Places** | (a) hierarchy/name enrichment (structured), (b) `link_parent_place_ids` | `enrich_places` | always |
| 3/6 | **Places** | **Geocoding cascade** → coordinates + provenance | `places_grok_geocode.cascade_geocoder` | always |
| 4/6 | **Bibliography** | ISBN / copyright / archive URLs, then source resolution (NARA, Archive.org, LOC, Gutenberg) | `supplemental_advanced.enrich_bibliography` + `bibliography_resolver` | always |
| 5/6 | **People + Equipment** | OpenSERP images / academic sources | `openserp_enrichment` | `supplemental_material.use_openserp` |
| 6/6 | **Weather** | NOAA GHCND station-observed data (supplements Open-Meteo) | `noaa_weather.enrich_weather_with_noaa` | `api.noaa_api_token` set |

> **Moved to Phase 2 (no longer a Phase-3 step):** people Grokipedia/Wikipedia biography +
> portrait + award citations; equipment Wikipedia extract + images (`equipment_wikipedia` —
> old step 4b); people_groups Wikipedia text + images (`groups_wikipedia` — old step 4c);
> places Grokipedia/Wikipedia descriptive text + image. These now run in the Phase-2
> extractors with native commit. The `enrich_all_people` / `enrich_all_groups_wikipedia` /
> `enrich_all_equipment_wikipedia` functions remain defined for CLI/tests but are **not** wired
> into the Phase-3 pipeline.

### Places geocoding cascade (step 3)
`enrich_all_places` sets hierarchy/names but **not** coordinates. The geocoding cascade wires
them in: **Nominatim (OSM)** first (free, cached, policy-compliant) → **hill/terrain**
geocoder for height features → **Grok** fallback for the rest. Writes WGS84 coordinates +
geocode provenance (`source`, `confidence`); low-confidence hits are flagged, never
fabricated. (This is also the coordinate source the maps overlay inherits via resolved
PlaceID.)

### Entities with NO Phase-3 enrichment (by design)
**Dates, Logistics, Casualties, Maps, Supplemental narrative, Events, and map_features** are
extracted + cross-referenced in Phase 2 but have **no external enrichment step**. Their
feature docs state this explicitly. (map_features has no Phase-3 wiring yet — see Known Gaps.)

---

## Reliability model

- **Per-source isolation:** every step runs via `_run_step`, which captures a source's
  outcome into `source_stats` (`enriched` count + `ok`/`error` + error text). A source that
  throws is **logged and recorded, never swallowed, and never aborts the rest of Phase 3** —
  one failing enrichment source does not block the others.
- **Surfacing:** failures are written to `output/.phase_results.json` (`source_stats` +
  `errored_sources`) which the completion notification relays to **email + Slack**, so
  partial success and previously-silent failures reach the operator.
- **Geocoding breakdown** is recorded in full (attempted / geocoded / not_found /
  low_confidence / errors) so partial geocoding is visible.

## Batch mode (`--batch`)

With `--batch`, Phase 3 first runs in collect-only mode (queuing Grok requests), submits them
to the **xAI Batch API** (≈50% cost), waits, then **re-runs the enrichment steps** with the
batched results served from cache. The re-run covers people/groups/places/bibliography.

## Idempotency / incremental

- Enrichment uses the shared `enrichment_gate` (staleness check + last-checked stamp) so
  entities are not re-enriched on every run.
- AWS mode: `manifest#phase2` scopes Phase 3 to only files changed by Phase 2 + dedup review
  (full-directory fallback if absent).

## CLI

```bash
python phase3_enrich_data.py --output-dir output        # all entities
python phase3_enrich_data.py --people-only              # people only
python phase3_enrich_data.py --no-references            # skip reference following (faster)
python phase3_enrich_data.py --batch                    # xAI Batch API (50% off)
python phase3_enrich_data.py --max-items N              # cap per entity type
```

## Known gaps

- **map_features has no Phase-3 step** — the map-interior extraction + reverse-registration
  (`src/extraction/map_registration.py`) are prototype-level and not wired into
  `phase3_enrich_data.py`. When maps productionize, a gated map step (NOAA-style) would run
  extraction → entity resolution → registration here.

---

## Related
- [PIPELINE.md](PIPELINE.md) — full four-phase overview.
- [../SHARED_HELPERS.md](../SHARED_HELPERS.md) — `enrichment_gate`, geocoding, translation.
- Per-entity enrichment detail: each `../features/<entity>/README.md` "Phase 3" section.
