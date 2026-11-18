# Phase 3: Enrichment — Authoritative Overview

**Source of truth:** `phase3_enrich_data.py` (orchestrator) + `src/enrichment/*` and the
`src/extraction/enrich_*` modules. This doc describes what actually runs; per-entity feature
docs point here.

For the verified per-entity map, how OpenSERP is engaged, and the known gaps, see
[PHASE3_MAP.md](PHASE3_MAP.md).

Phase 3 takes the extracted + deduplicated entities (`output/<entity>/*.json`) and augments
them with data from external sources. It runs after the dedup review gate; in AWS mode it is
auto-triggered when Phase 2 finds no duplicates. Phase 3 scopes its download by the per-book `book_manifest#` rows (`ecs_entrypoint._get_book_entity_files`, `src/utils/book_manifest.py`), else lists the entity prefixes in S3 (`_download_phase3_from_s3`); it does not read `manifest#phase2`.

**Enrichment is split by SOURCE (not by entity):**
- **Phase 2** owns **all Grokipedia + Wikipedia** enrichment (descriptive text + images),
  committed natively by each extractor as it writes the entity — people (biography, portrait,
  award citations), people_groups (Wikipedia text + images), places (Grokipedia/Wikipedia
  descriptive text + image), equipment (Wikipedia extract + images). See each feature doc.
- **Phase 3** owns **every other external source**: geocoding (Nominatim/Grok coordinates),
  NOAA weather, OpenSERP (images, web pages, primary sources), NARA / Archive.org bibliography
  resolution, plus the **structured** hierarchy/org-history Grok passes that promote facts into
  spec fields (place hierarchy/names; group unit-history) — these are structured-field
  enrichment, not descriptive Grokipedia/Wikipedia text, so they stay in Phase 3.

---

## Step order

The code runs these in a fixed order inside `phase3_enrich_data.py::main`. Gates, per-entity skip rules,
fields written, the geocoding cascade, the OpenSERP group and the batch re-run are described once, in
[PHASE3_MAP.md](PHASE3_MAP.md) (sections 1 to 5).

| Order | Step | Entity |
|---|---|---|
| 1 | `people_groups` | People Groups: structured unit and organisation facts |
| 2 | `places`, `link_parent_places`, `geocode` | Places: hierarchy and names, then coordinates |
| 3 | `bibliography` | Bibliography: alt titles (ISBN, copyright and archive checks are off by default) |
| 4 | `bibliography_resolve` | Bibliography: find online copies (NARA, OpenSERP, Archive.org, Gutenberg, Grok) |
| 5 | `in_doc_map_links`, `image_map_vision` | Maps and Images from the source documents |
| 6 | `openserp_*` (and five non-search steps in the same function) | People, Equipment, People Groups, Places, Source Section; gated by `supplemental_material.use_openserp` |
| 7 | `equipment_images` | Equipment: vision-confirmed images |
| 8 | `noaa_weather` | Weather; needs `api.noaa_api_token` |

`--people-only` skips every step (people enrichment moved to Phase 2), so it is effectively a no-op.

> **Moved to Phase 2 (no longer a Phase-3 step):** people Grokipedia/Wikipedia biography +
> portrait + award citations; equipment Wikipedia extract + images; people_groups Wikipedia text +
> images; places Grokipedia/Wikipedia descriptive text + image. These run in the Phase-2 extractors
> with native commit. `enrich_all_people`, `enrich_all_groups_wikipedia` and
> `enrich_all_equipment_wikipedia` remain defined for CLI/tests but are not wired into Phase 3.

### Entities with NO Phase-3 enrichment (by design)
**Dates, Logistics, Casualties, Supplemental narrative, and map_features** are extracted and
cross-referenced in Phase 2 but have no external enrichment step. Events and sub-events only receive a
deterministic `SourceSectionID` stamp. (map_features has no Phase-3 wiring yet; see Known Gaps.)

---

## Reliability model

Every step runs through `_run_step`: a source that throws is logged, recorded in `source_stats` and listed in
`output/.phase_results.json` (`errored_sources`), which the completion notification relays to email and
Slack. One failing source does not stop the others. Geocoding is recorded directly in `source_stats`
with its attempted, geocoded, not_found, low_confidence and error counts. Details:
[PHASE3_MAP.md](PHASE3_MAP.md) section 1.

## CLI

```bash
python phase3_enrich_data.py --output-dir output        # all entities
python phase3_enrich_data.py --people-only              # skips all steps (people enrichment is Phase 2)
python phase3_enrich_data.py --no-references            # parsed and logged only; no effect
python phase3_enrich_data.py --batch                    # xAI Batch API (50% off); see PHASE3_MAP.md section 5
python phase3_enrich_data.py --max-items N              # cap per entity type
```

## Known gaps

Listed in [TODO.md, Phase 3 review](../TODO.md#phase-3-work-order-dependency-grouped-2026-11-17).

---

## Related
- [PIPELINE.md](PIPELINE.md) — full four-phase overview.
- [../SHARED_HELPERS.md](../SHARED_HELPERS.md) — `enrichment_gate`, geocoding, translation.
- Per-entity enrichment detail: each `../features/<entity>/README.md` "Phase 3" section.
