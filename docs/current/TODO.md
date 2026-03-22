# TODO

**Last Updated:** 2026-03-22

---

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

### Wire `supplemental_advanced.py` to `output/bibliography/`
**Priority:** Medium
**Status:** Not Started

Phase 3 enrichment still reads from old `output/supplemental/` location. Needs to read from `output/bibliography/` and enrich bibliography entries with ISBN, copyright status, and author death dates.

### Casualties spec review
**Priority:** Medium
**Status:** Not Started

No formal spec exists for casualties. Known issues:
- 77 `impacted_equipment.EquipmentID` are null (no match found during extraction)
- 4 `event_context.EventID` broken refs
- `source.EventID` not populated (event context is in `event_context` instead)
- `count` field has mixed types (integer and string)

### Event→People orphaned refs
**Priority:** Medium
**Status:** Not Started

277 unique PersonIDs referenced in event `Sub-events[].people[]` that have no matching file in `output/people/`. Likely from deduplication/merging — old IDs not updated in event files.

### Places parent_place_id fake ULIDs
**Priority:** Low
**Status:** Not Started

4 place files (england, london, leningrad, berlin) have hand-crafted placeholder IDs like `01ENGLAND0000000000000000` in `hierarchy.parent_place_id`. Need real PlaceIDs or removal.

### Fix 5 people_groups with empty group_type
**Priority:** Low
**Status:** Not Started

Files: `u.s. assault division.json`, `eighteen divisions.json`, `363d infantry division.json`, `three assault divisions.json`, `77 parachute.json` — all have `group_type: ""`.

### Fix 1 weather file with date range
**Priority:** Low
**Status:** Not Started

`19440619 to 19440621_Channel_01KM4A04.json` has `date: "1944-06-19 to 1944-06-21"` instead of single YYYY-MM-DD.

---

## Windows PowerShell Scripts
**Priority:** Low
**Status:** Not Started

Create PowerShell equivalents for bash scripts to support Windows users. See `scripts/README.md` for full list.

---

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
