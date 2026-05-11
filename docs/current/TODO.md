# TODO

**Last Updated:** 2026-05-04

---

## Active

### Batch mode for optional extractors (casualties, weather, equipment, logistics, supplemental)
**Priority:** Medium
**Status:** Not Started

Optional entity extractors (step 5 of Phase 2) use real-time API calls even when `--batch` is passed. This includes casualties, weather, equipment, logistics, and supplemental/bibliography extraction. They run after the core batch completes because they depend on event data. Fix: add a second batch collect→submit→poll cycle for optional extractors. Requires separating request collection from result processing in each extractor.

### ~~Idle monitor not tearing down NAT Gateway~~
**Priority:** High
**Status:** ✅ Done (2026-05-03)

Rewritten to check ECS tasks instead of ALB. NAT manager Lambda handles create/delete lifecycle with DynamoDB lock. Idle monitor checks NAT age before teardown.

### Move ALB to dynamic lifecycle management
**Priority:** Medium
**Status:** ✅ Done (2026-05-06)

ALB removed entirely. Pipeline connects to OpenSERP via task private IP. NAT manager Lambda handles NAT + VPC endpoints only. Saves ~$16/month.

### Batch mode for Phase 3 enrichment Grok calls
**Priority:** Medium
**Status:** Not Started

Phase 3 biography, group, place, and bibliography enrichment make individual real-time Grok API calls per entity. These could be collected and submitted as a batch for 50% cost reduction. External search calls (Grokipedia, Wikipedia, OpenSERP) must remain real-time since they hit third-party APIs.

## Active

### Re-extract all entities with fixed prompts
**Priority:** High
**Status:** Pending (user will run)

After cache clear, re-run pipeline with updated prompts that request:
- Places: `original_text`, `role_in_event`, `related_places`, full 22-type geography_type enum
- People: `position_at_event`, `life_event`, `original_text`
- People groups: `context`, `original_text`
- Logistics: `context`, `paragraph_numbers`

### Run full place enrichment
**Priority:** High
**Status:** Pending (user will run)

`enrich_all_places()` for remaining ~1093 places without hierarchy. Only 46/1138 have hierarchy data. Command: `python3 phase3_enrich_data.py` or standalone.

### ~~Wire `supplemental_advanced.py` to `output/bibliography/`~~
**Priority:** Medium
**Status:** ✅ Done (2026-03-28)

`enrich_bibliography()` reads from `output/bibliography/`, enriches ISBN/copyright/archive URLs. Wired into `phase3_enrich_data.py`. Controlled by `supplemental_material` config flags (`extract_isbn`, `determine_copyright`, `verify_archive_urls`).

### ~~Casualties spec review~~
**Priority:** Medium
**Status:** ✅ Done (2026-05-01)

Spec rewritten: people-only (removed equipment/weather), added `side` field, updated schema and prompt.

### ~~Event→People orphaned refs~~
**Priority:** Medium
**Status:** ✅ Done (2026-04-19)

`scripts/fix_orphaned_person_refs.py` scans event `Sub-events[].people[]` and removes PersonIDs with no matching file in `output/people/`. Supports `--dry-run` and `--verbose`.

### ~~Places parent_place_id fake ULIDs~~
**Priority:** Low
**Status:** ✅ Done (2026-04-19)

`scripts/fix_fake_place_ulids.py` replaces hand-crafted placeholder IDs with real PlaceIDs from the index. Also fixed `_fix_invalid_ulids()` to catch lowercase `_id` fields (was only checking uppercase `ID`).

### ~~Fix 5 people_groups with empty group_type~~
**Priority:** Low
**Status:** ✅ Done (2026-05-01)

Fixed by re-extraction with updated prompts.

### Periodic re-search of not_found entities
**Priority:** Low
**Status:** Not Started

Entities marked `enrichment_status: "not_found"` are skipped permanently. The `last_enrichment_search` date field is now recorded. Add a config option (e.g., `enrichment.re_search_after_days: 90`) that re-searches entities whose `last_enrichment_search` is older than the threshold. Grokipedia and Wikipedia content changes over time — new articles, corrections, and additions may provide data for previously unfindable people.

### ~~Fix 1 weather file with date range~~
**Priority:** Low
**Status:** ✅ Done (fixed by re-extraction)

---

## Windows PowerShell Scripts
**Priority:** Low
**Status:** Not Started

Create PowerShell equivalents for bash scripts to support Windows users. See `scripts/README.md` for full list.

---

## Completed (2026-03-28)

- ✅ Rate limiter: thread-safe token-bucket in `GrokClient` (30 calls/min default, configurable via `config.yaml`)
- ✅ Empty event file retry: `_retry_missing_events` now re-extracts chapters with 0 sub-events
- ✅ `find_related_groups.py`: fixed missing `cache_dir` argument to `GrokClient()`
- ✅ Map URL parsing: fixed `%20target=` HTML attribute leak in 3 source files + parser guard
- ✅ Equipment indexing: `load_equipment_index` and `generate_equipment_index` skip `.processed_events.json`
- ✅ OpenSERP setup: auto-detects OS/architecture, cross-compiles, builds both `search_maps` and `search_media`
- ✅ Duplicate return removed in `casualties.py`

## Completed (2026-03-29)

- ✅ xAI Batch API integration (`--batch` flag) for 50% cost reduction on Phase 2 and Phase 3
- ✅ Event extraction token optimization: paragraph numbers instead of fulltext echo (~72% output token reduction)

## Completed (2026-03-22)

- ✅ Weather: removed dead `_extract_weather_for_sub_event`, fixed DateID hallucination, normalized empty strings, removed non-spec `country` field
- ✅ Cross-reference consistency: all DateMentionID/PlaceMentionID now point to top-level entity IDs (weather, places, people, groups, images, maps)
- ✅ Code fixes: `batch_parallel.py`, `weather_central.py`, `images.py`, `external_maps.py`, `casualties.py` — all resolve entity IDs from lookups instead of trusting LLM
- ✅ Places: backfilled current_name, geography_type, coordinates, bounding_box, map_urls, date linking
- ✅ People groups: fixed group_type to spec enum, backfilled date linking
- ✅ SCHEMA_REFERENCE.md: comprehensive schema documentation for all 11 entity types
- ✅ Archived 8 stale docs to `docs/archive/2026-03-22/`

## Completed (2026-03-19)

- ✅ Batch weather/logistics/casualties extraction (1 API call per chapter)
- ✅ Supplemental split architecture (bibliography + factual content)
- ✅ ULID fixing, JSON schema validation, output validation script

### True batch submission for OpenSERP verification
**Priority:** Low
**Status:** Not Started

OpenSERP verification calls (Grok YES/NO for each search result) currently run synchronously. Results are cached so repeat runs are free, but the first run still makes ~1,000 individual API calls. Redesign to: (1) collect all candidate results in Pass 1, (2) submit all verification prompts as a single Grok batch job, (3) poll for completion, (4) apply results in Pass 2. Requires decoupling search and verify steps, possibly across separate ECS task invocations.

### Unmatched combinable people files
**Priority:** Medium
**Status:** Not Started

Dedup scoring missed these obvious matches — likely because one entry uses a title/role instead of a name, or the name variants are too different for fuzzy matching:

- `hitler.json` / `adolf hitler.json`
- `dwight d. eisenhower.json` / `eisenhower.json` / `supreme commander.json` / `supreme commander allied expeditionary force.json` / `supreme commander, allied expeditionary force.json` / `supreme command.json` / `supreme allied commander.json`
- `george patton.json` / `george s. patton, jr..json`

Fix: either improve dedup scoring to match titles/roles to people, or add a manual merge pass for known high-profile individuals.
